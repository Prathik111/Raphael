"""Tamper-evident audit log over the existing database (Gate 31).

Operational events (EventBus) say what happened; audit records say who
did what to which resource under which authorization, chained by hash
so modification is detectable. Storage is append-oriented: records are
never updated in place, and pruning requires an explicit JSONL export
first (rotation), leaving a checkpoint behind.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from ai_ecosystem.core.models.base import Entity, utcnow
from pydantic import Field


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _chain_hash(prev_hash: str, body: str) -> str:
    return hashlib.sha256(f"{prev_hash}|{body}".encode()).hexdigest()


# Volatile storage fields excluded from the chain body (create() touches
# updated_at after hashing; the chain covers content + linkage instead).
_VOLATILE = {"record_hash", "updated_at"}


def _body_of(record: AuditRecord) -> str:
    return _canonical({k: v for k, v in record.model_dump().items() if k not in _VOLATILE})


class AuditRecord(Entity):
    """One immutable audit entry with its chain link."""

    timestamp: datetime = Field(default_factory=utcnow)
    actor: str = ""
    actor_type: str = ""
    task_id: str = ""
    agent_id: str = ""
    action: str = ""
    resource: str = ""
    decision: str = ""
    risk: str = ""
    authorization: str = ""
    result: str = ""
    correlation_id: str = ""
    prev_hash: str = "GENESIS"
    record_hash: str = ""


class AuditLog:
    """Append-only hash-chained audit trail (same database, own table)."""

    def __init__(self, db: Any) -> None:
        import threading

        from ai_ecosystem.core.persistence.sqlite import _SnapshotTable

        self._db = db
        self._t = _SnapshotTable(db, "audit_log", AuditRecord)
        self._append_lock = threading.Lock()
        self._tail_hash: str | None = None

    def record(
        self,
        action: str,
        actor: str = "",
        actor_type: str = "",
        task_id: str = "",
        agent_id: str = "",
        resource: str = "",
        decision: str = "",
        risk: str = "",
        authorization: str = "",
        result: str = "",
        correlation_id: str = "",
    ) -> AuditRecord:
        """Append one record, linked to the previous hash.

        The read-tail/hash/append sequence holds a process lock so
        concurrent recorders cannot fork the chain. The tail hash is
        tracked in memory (not re-derived from timestamp ordering,
        which can tie on coarse clocks).
        """
        with self._append_lock:
            if self._tail_hash is None:
                previous = self._t.list()
                self._tail_hash = previous[-1].record_hash if previous else "GENESIS"
            prev_hash = self._tail_hash
            record = AuditRecord(
                timestamp=utcnow(),
                actor=actor,
                actor_type=actor_type,
                task_id=task_id,
                agent_id=agent_id,
                action=action,
                resource=resource,
                decision=decision,
                risk=risk,
                authorization=authorization,
                result=result,
                correlation_id=correlation_id,
                prev_hash=prev_hash,
            )
            record.record_hash = _chain_hash(prev_hash, _body_of(record))
            created = self._t.create(record)
            self._tail_hash = created.record_hash
            return created

    def verify(self) -> tuple[bool, str]:
        """Walk the chain by linkage (immune to timestamp ties).

        Every record must hash correctly, link exactly once, and sit on
        the single GENESIS-rooted chain. Retention works through rotate()
        (export + fresh chain), so live data never has legal gaps.
        """
        records = self._t.list()
        for record in records:
            if not self._hash_ok(record):
                return False, record.id
        by_prev: dict[str, list[AuditRecord]] = {}
        for record in records:
            by_prev.setdefault(record.prev_hash, []).append(record)
        if any(len(group) > 1 for group in by_prev.values()):
            return False, "fork detected"
        heads = by_prev.get("GENESIS", [])
        if not records:
            return True, ""
        if len(heads) != 1:
            return False, "multiple chain heads"
        on_chain = 0
        current: AuditRecord | None = heads[0]
        while current is not None:
            on_chain += 1
            nxt = by_prev.get(current.record_hash, [])
            current = nxt[0] if nxt else None
        if on_chain != len(records):
            return False, "gap in chain"
        return True, ""

    @staticmethod
    def _hash_ok(record: AuditRecord) -> bool:
        return record.record_hash == _chain_hash(record.prev_hash, _body_of(record))

    def query(
        self,
        actor: str = "",
        task_id: str = "",
        action: str = "",
        correlation_id: str = "",
        limit: int = 100,
    ) -> list[AuditRecord]:
        """Filter records (all filters AND-combined, newest last)."""
        matches = [
            record
            for record in self._t.list()
            if (not actor or record.actor == actor)
            and (not task_id or record.task_id == task_id)
            and (not action or record.action == action)
            and (not correlation_id or record.correlation_id == correlation_id)
        ]
        return sorted(matches, key=lambda r: r.created_at)[-max(0, limit) :]

    def export(self, path: str) -> int:
        """Write the chain to JSONL (rotation prerequisite)."""
        records = sorted(self._t.list(), key=lambda r: r.created_at)
        with open(path, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(record.model_dump_json() + "\n")
        return len(records)

    def rotate(self, archive_path: str) -> int:
        """Export then clear, leaving a checkpoint record behind."""
        count = self.export(archive_path)
        for record in self._t.list():
            self._t.delete(record.id)
        self._tail_hash = None  # fresh chain for checkpoint
        self.record(
            action="audit.rotate",
            actor="system",
            actor_type="system",
            result=f"archived {count} records to {archive_path}",
        )
        return count

    def purge_older_than(
        self, days: float, archive_path: str, now: datetime | None = None
    ) -> int:
        """Retention by archival rotation: export olds, then clear all.

        Physical deletion without export would break the chain, so
        retention always archives first (same guarantee as rotate()).
        Returns the number of archived records.
        """
        moment = now or utcnow()
        olds = [
            record
            for record in self._t.list()
            if (moment - record.created_at).total_seconds() / 86400.0 > days
        ]
        if not olds:
            return 0
        return self.rotate(archive_path)

    def as_recorder(self) -> Any:
        """ToolRunner-compatible auditor hook (duck-typed, no imports)."""
        log = self

        def hook(payload: dict) -> None:
            log.record(
                action=str(payload.get("action", "tool.execute")),
                actor=str(payload.get("agent_id", "")),
                actor_type="agent",
                task_id=str(payload.get("task_id", "")),
                agent_id=str(payload.get("agent_id", "")),
                resource=str(payload.get("tool", "")),
                decision=str(payload.get("decision", "")),
                risk=str(payload.get("risk", "")),
                authorization=str(payload.get("reason", "")),
                result="success" if payload.get("success") else "failure",
                correlation_id=str(payload.get("call_id", "")),
            )

        return hook
