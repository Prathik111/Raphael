"""SQLite persistence (BUILD_PLAN Gate 3, Part 2).

One relational database, snapshot-per-row storage (domain objects as
JSON), explicit transactions, idempotent migrations. Crash recovery works
because every state change is committed before the caller proceeds, and
: path:`contexts` always holds the latest restorable snapshot per task.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional, TypeVar

from ai_ecosystem.core.errors.exceptions import PersistenceError
from ai_ecosystem.core.events.bus import Event, EventStore
from ai_ecosystem.core.models.base import utcnow
from ai_ecosystem.core.models.domain import (
    Agent,
    Device,
    Entity,
    ExecutionContext,
    Memory,
    Plan,
    Skill,
    Task,
    VerificationResult,
)
from ai_ecosystem.core.persistence.repositories import (
    AgentRepository,
    DeviceRepository,
    EventRepository,
    ExecutionContextRepository,
    LearningProposalRepository,
    MemoryRepository,
    MessageRepository,
    PlanRepository,
    SkillRepository,
    SnapshotRepository,
    SnapshotRepository,
    TaskRepository,
    UsageEventRepository,
    UsagePatternRepository,
    VerificationRepository,
)
from ai_ecosystem.learning.models import (
    LearningProposal,
    UsageEvent,
    UsagePattern,
)
from ai_ecosystem.system.monitor.models import SystemSnapshot

SCHEMA_VERSION = 1

_DDL = [
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS contexts (task_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, snapshot TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS skills (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    # Raw job envelopes; the ComputeJob model arrives in Gate 25.
    "CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS verifications (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS personalities (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS preferences (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS system_snapshots (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS usage_events (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS usage_patterns (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS learning_proposals (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS agent_definitions (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS agent_tasks (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS agent_messages (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS compute_jobs (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS device_presence (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS audit_log (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS learning_policy (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS scheduler_jobs (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)",
]

T = TypeVar("T", bound=Entity)


class Database:
    """Owns one SQLite connection; thread-safe via an internal lock."""

    def __init__(self, path: str = ":memory:") -> None:
        try:
            self._conn = sqlite3.connect(
                path, check_same_thread=False, isolation_level=None
            )
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot open database {path!r}: {exc}") from exc
        self._lock = threading.RLock()
        self._tx_depth = 0

    def migrate(self) -> int:
        """Create/upgrade schema idempotently; return schema version."""
        try:
            with self._lock:
                for stmt in _DDL:
                    self._conn.execute(stmt)
                self._conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
        except sqlite3.Error as exc:
            raise PersistenceError(f"migration failed: {exc}") from exc
        return SCHEMA_VERSION

    def schema_version(self) -> Optional[int]:
        """Current schema version, or None before the first migrate()."""
        rows = self.query("SELECT value FROM meta WHERE key='schema_version'")
        return int(rows[0][0]) if rows else None

    @contextmanager
    def transaction(self) -> Iterator["Database"]:
        """Atomic block: exception rolls everything back (may nest)."""
        with self._lock:
            outermost = self._tx_depth == 0
            if outermost:
                self._conn.execute("BEGIN IMMEDIATE")
            self._tx_depth += 1
        try:
            yield self
        except Exception:
            with self._lock:
                self._tx_depth -= 1
                if outermost:
                    self._conn.execute("ROLLBACK")
            raise
        else:
            with self._lock:
                self._tx_depth -= 1
                if outermost:
                    self._conn.execute("COMMIT")

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Run SQL under the lock; autocommits unless in transaction().

        Prefer query()/write(): cursors must not escape the lock, since
        concurrent threads sharing this connection would otherwise
        interleave fetches and read each other's rows.
        """
        with self._lock:
            try:
                return self._conn.execute(sql, params)
            except sqlite3.Error as exc:
                raise PersistenceError(f"database error: {exc}") from exc

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        """SELECT with fetch-all performed atomically under the lock."""
        with self._lock:
            try:
                return self._conn.execute(sql, params).fetchall()
            except sqlite3.Error as exc:
                raise PersistenceError(f"database error: {exc}") from exc

    def write(self, sql: str, params: tuple = ()) -> tuple[int, int]:
        """INSERT/UPDATE/DELETE; returns (rowcount, lastrowid) atomically."""
        with self._lock:
            try:
                cursor = self._conn.execute(sql, params)
                return cursor.rowcount, int(cursor.lastrowid or 0)
            except sqlite3.Error as exc:
                raise PersistenceError(f"database error: {exc}") from exc

    def close(self) -> None:
        """Close the connection (idempotent)."""
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def backup_to(self, path: str) -> str:
        """Consistent online backup to a new file (SQLite backup API)."""
        try:
            target = sqlite3.connect(path)
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot open backup {path!r}: {exc}") from exc
        try:
            with self._lock:
                self._conn.backup(target)
        except sqlite3.Error as exc:
            raise PersistenceError(f"backup failed: {exc}") from exc
        finally:
            target.close()
        return path


class _SnapshotTable:
    """Generic id/snapshot store shared by the entity repositories."""

    def __init__(self, db: Database, table: str, model_cls: type[T]) -> None:
        self._db = db
        self._table = table
        self._model_cls = model_cls

    def create(self, item: T) -> T:
        item.touch()
        self._db.write(
            f"INSERT INTO {self._table} (id, snapshot, updated_at) VALUES (?, ?, ?)",
            (item.id, item.model_dump_json(), item.updated_at.isoformat()),
        )
        return item

    def get(self, item_id: str) -> Optional[T]:
        rows = self._db.query(
            f"SELECT snapshot FROM {self._table} WHERE id = ?", (item_id,)
        )
        return self._model_cls.model_validate_json(rows[0][0]) if rows else None

    def update(self, item: T) -> T:
        item.touch()
        rowcount, _ = self._db.write(
            f"UPDATE {self._table} SET snapshot = ?, updated_at = ? WHERE id = ?",
            (item.model_dump_json(), item.updated_at.isoformat(), item.id),
        )
        if rowcount == 0:
            return self.create(item)
        return item

    def delete(self, item_id: str) -> bool:
        rowcount, _ = self._db.write(
            f"DELETE FROM {self._table} WHERE id = ?", (item_id,)
        )
        return rowcount > 0

    def list(self) -> list[T]:
        rows = self._db.query(
            f"SELECT snapshot FROM {self._table} ORDER BY updated_at"
        )
        return [self._model_cls.model_validate_json(r[0]) for r in rows]


class SqliteTaskRepository(TaskRepository):
    """Durable tasks."""

    def __init__(self, db: Database) -> None:
        self._t = _SnapshotTable(db, "tasks", Task)

    def create(self, item: Task) -> Task:
        return self._t.create(item)

    def get(self, item_id: str) -> Optional[Task]:
        return self._t.get(item_id)

    def update(self, item: Task) -> Task:
        return self._t.update(item)

    def delete(self, item_id: str) -> bool:
        return self._t.delete(item_id)

    def list(self) -> list[Task]:
        return self._t.list()


class SqlitePlanRepository(PlanRepository):
    """Durable plans."""

    def __init__(self, db: Database) -> None:
        self._t = _SnapshotTable(db, "plans", Plan)

    def create(self, item: Plan) -> Plan:
        return self._t.create(item)

    def get(self, item_id: str) -> Optional[Plan]:
        return self._t.get(item_id)

    def update(self, item: Plan) -> Plan:
        return self._t.update(item)

    def delete(self, item_id: str) -> bool:
        return self._t.delete(item_id)

    def list(self) -> list[Plan]:
        return self._t.list()


class SqliteMemoryRepository(MemoryRepository):
    """Durable memory records."""

    def __init__(self, db: Database) -> None:
        self._t = _SnapshotTable(db, "memories", Memory)

    def create(self, item: Memory) -> Memory:
        return self._t.create(item)

    def get(self, item_id: str) -> Optional[Memory]:
        return self._t.get(item_id)

    def update(self, item: Memory) -> Memory:
        return self._t.update(item)

    def delete(self, item_id: str) -> bool:
        return self._t.delete(item_id)

    def list(self) -> list[Memory]:
        return self._t.list()


class SqliteSkillRepository(SkillRepository):
    """Durable skills."""

    def __init__(self, db: Database) -> None:
        self._t = _SnapshotTable(db, "skills", Skill)

    def create(self, item: Skill) -> Skill:
        return self._t.create(item)

    def get(self, item_id: str) -> Optional[Skill]:
        return self._t.get(item_id)

    def update(self, item: Skill) -> Skill:
        return self._t.update(item)

    def delete(self, item_id: str) -> bool:
        return self._t.delete(item_id)

    def list(self) -> list[Skill]:
        return self._t.list()


class SqliteAgentRepository(AgentRepository):
    """Durable agent identities."""

    def __init__(self, db: Database) -> None:
        self._t = _SnapshotTable(db, "agents", Agent)

    def create(self, item: Agent) -> Agent:
        return self._t.create(item)

    def get(self, item_id: str) -> Optional[Agent]:
        return self._t.get(item_id)

    def update(self, item: Agent) -> Agent:
        return self._t.update(item)

    def delete(self, item_id: str) -> bool:
        return self._t.delete(item_id)

    def list(self) -> list[Agent]:
        return self._t.list()


class SqlitePresenceRepository:
    """Durable presence rows (model imported lazily: cloud packages must
    never load beneath persistence)."""

    def __init__(self, db: Database) -> None:
        from ai_ecosystem.cloud.availability import DevicePresence

        self._t = _SnapshotTable(db, "device_presence", DevicePresence)

    def create(self, item: "DevicePresence") -> "DevicePresence":
        """Persist a presence row."""
        return self._t.create(item)

    def get(self, item_id: str) -> "Optional[DevicePresence]":
        """Fetch by record id."""
        return self._t.get(item_id)

    def update(self, item: "DevicePresence") -> "DevicePresence":
        """Replace the stored row."""
        return self._t.update(item)

    def delete(self, item_id: str) -> bool:
        """Remove a row."""
        return self._t.delete(item_id)

    def list(self) -> "list[DevicePresence]":
        """All rows."""
        return self._t.list()


class SqliteDeviceRepository(DeviceRepository):
    """Durable device records."""

    def __init__(self, db: Database) -> None:
        self._t = _SnapshotTable(db, "devices", Device)

    def create(self, item: Device) -> Device:
        return self._t.create(item)

    def get(self, item_id: str) -> Optional[Device]:
        return self._t.get(item_id)

    def update(self, item: Device) -> Device:
        return self._t.update(item)

    def delete(self, item_id: str) -> bool:
        return self._t.delete(item_id)

    def list(self) -> list[Device]:
        return self._t.list()


class SqliteVerificationRepository(VerificationRepository):
    """Durable verification outcomes."""

    def __init__(self, db: Database) -> None:
        self._t = _SnapshotTable(db, "verifications", VerificationResult)

    def create(self, item: VerificationResult) -> VerificationResult:
        return self._t.create(item)

    def get(self, item_id: str) -> Optional[VerificationResult]:
        return self._t.get(item_id)

    def update(self, item: VerificationResult) -> VerificationResult:
        return self._t.update(item)

    def delete(self, item_id: str) -> bool:
        return self._t.delete(item_id)

    def list(self) -> list[VerificationResult]:
        return self._t.list()


class SqliteSnapshotRepository(SnapshotRepository):
    """Durable system snapshots."""

    def __init__(self, db: Database) -> None:
        self._t = _SnapshotTable(db, "system_snapshots", SystemSnapshot)

    def create(self, item: SystemSnapshot) -> SystemSnapshot:
        return self._t.create(item)

    def get(self, item_id: str) -> Optional[SystemSnapshot]:
        return self._t.get(item_id)

    def update(self, item: SystemSnapshot) -> SystemSnapshot:
        return self._t.update(item)

    def delete(self, item_id: str) -> bool:
        return self._t.delete(item_id)

    def list(self) -> list[SystemSnapshot]:
        return self._t.list()

    def latest(self) -> Optional[SystemSnapshot]:
        """Most recently collected snapshot (None when none stored)."""
        snapshots = self._t.list()
        if not snapshots:
            return None
        return sorted(snapshots, key=lambda s: s.collected_at)[-1]


class SqliteUsageEventRepository(UsageEventRepository):
    """Durable usage events."""

    def __init__(self, db: Database) -> None:
        self._t = _SnapshotTable(db, "usage_events", UsageEvent)

    def create(self, item: UsageEvent) -> UsageEvent:
        return self._t.create(item)

    def get(self, item_id: str) -> Optional[UsageEvent]:
        return self._t.get(item_id)

    def update(self, item: UsageEvent) -> UsageEvent:
        return self._t.update(item)

    def delete(self, item_id: str) -> bool:
        return self._t.delete(item_id)

    def list(self) -> list[UsageEvent]:
        return self._t.list()


class SqliteUsagePatternRepository(UsagePatternRepository):
    """Durable usage patterns."""

    def __init__(self, db: Database) -> None:
        self._t = _SnapshotTable(db, "usage_patterns", UsagePattern)

    def create(self, item: UsagePattern) -> UsagePattern:
        return self._t.create(item)

    def get(self, item_id: str) -> Optional[UsagePattern]:
        return self._t.get(item_id)

    def update(self, item: UsagePattern) -> UsagePattern:
        return self._t.update(item)

    def delete(self, item_id: str) -> bool:
        return self._t.delete(item_id)

    def list(self) -> list[UsagePattern]:
        return self._t.list()


class SqliteLearningProposalRepository(LearningProposalRepository):
    """Durable learning-proposal review queue."""

    def __init__(self, db: Database) -> None:
        self._t = _SnapshotTable(db, "learning_proposals", LearningProposal)

    def create(self, item: LearningProposal) -> LearningProposal:
        return self._t.create(item)

    def get(self, item_id: str) -> Optional[LearningProposal]:
        return self._t.get(item_id)

    def update(self, item: LearningProposal) -> LearningProposal:
        return self._t.update(item)

    def delete(self, item_id: str) -> bool:
        return self._t.delete(item_id)

    def list(self) -> list[LearningProposal]:
        return self._t.list()


class SqliteMessageRepository(MessageRepository):
    """Durable agent messages (model imported lazily: agent.multi must
    never load beneath persistence)."""

    def __init__(self, db: Database) -> None:
        from ai_ecosystem.agent.multi.messages import AgentMessage

        self._t = _SnapshotTable(db, "agent_messages", AgentMessage)

    def create(self, item: Any) -> Any:
        """Persist a sent message."""
        return self._t.create(item)

    def get(self, item_id: str) -> Optional[Any]:
        """Fetch by message id."""
        return self._t.get(item_id)

    def update(self, item: Any) -> Any:
        """Replace the stored message."""
        return self._t.update(item)

    def delete(self, item_id: str) -> bool:
        """Remove a message."""
        return self._t.delete(item_id)

    def list(self) -> list[Any]:
        """All messages in creation order."""
        return self._t.list()


class SqliteExecutionContextRepository(ExecutionContextRepository):
    """Latest restorable snapshot per task (crash-recovery source of truth)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, context: ExecutionContext) -> ExecutionContext:
        context.touch()
        self._db.write(
            "INSERT INTO contexts (task_id, snapshot, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(task_id) DO UPDATE SET snapshot=excluded.snapshot,"
            " updated_at=excluded.updated_at",
            (context.task_id, context.snapshot(), context.updated_at.isoformat()),
        )
        return context

    def load(self, task_id: str) -> Optional[ExecutionContext]:
        rows = self._db.query(
            "SELECT snapshot FROM contexts WHERE task_id = ?", (task_id,)
        )
        return ExecutionContext.restore(rows[0][0]) if rows else None

    def delete(self, task_id: str) -> bool:
        rowcount, _ = self._db.write(
            "DELETE FROM contexts WHERE task_id = ?", (task_id,))
        return rowcount > 0


class SqliteEventRepository(EventRepository):
    """Durable event log (serialized envelopes)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def append_snapshot(self, snapshot: str) -> int:
        _, lastrowid = self._db.write(
            "INSERT INTO events (snapshot) VALUES (?)", (snapshot,)
        )
        return lastrowid

    def list_snapshots(self) -> list[str]:
        rows = self._db.query("SELECT snapshot FROM events ORDER BY seq")
        return [r[0] for r in rows]


class DbEventStore(EventStore):
    """EventStore backed by the relational event log."""

    def __init__(self, repo: SqliteEventRepository) -> None:
        self._repo = repo

    def append(self, event: Event) -> int:
        return self._repo.append_snapshot(event.model_dump_json())

    def list(self) -> list[Event]:
        return [Event.model_validate_json(s) for s in self._repo.list_snapshots()]

    def replay(self, handler: Any) -> int:
        count = 0
        for event in self.list():
            handler(event)
            count += 1
        return count

    def attach(self, bus: Any) -> None:
        """Persist everything published on ``bus`` (bus.subscribe_all)."""
        bus.subscribe_all(self.append_and_ignore)

    def append_and_ignore(self, event: Event) -> None:
        """``subscribe``-compatible wrapper discarding the sequence number."""
        self.append(event)


class SqliteJobStore:
    """Raw job envelopes keyed by job_id (ComputeJob model lands Gate 25)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, job_id: str, envelope: dict) -> dict:
        import json

        self._db.write(
            "INSERT INTO jobs (job_id, snapshot, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(job_id) DO UPDATE SET snapshot=excluded.snapshot,"
            " updated_at=excluded.updated_at",
            (job_id, json.dumps(envelope), utcnow().isoformat()),
        )
        return envelope

    def load(self, job_id: str) -> Optional[dict]:
        import json

        rows = self._db.query(
            "SELECT snapshot FROM jobs WHERE job_id = ?", (job_id,)
        )
        return json.loads(rows[0][0]) if rows else None
