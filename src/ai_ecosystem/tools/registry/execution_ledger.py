"""Durable execution identity and crash-recovery ledger."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from ai_ecosystem.core.models.base import utcnow
from ai_ecosystem.core.persistence.sqlite import Database


class ExecutionState(str, Enum):
    PLANNED = "PLANNED"
    AUTHORIZED = "AUTHORIZED"
    STARTED = "STARTED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class ExecutionRecord(BaseModel):
    action_hash: str
    task_id: str
    tool: str
    state: ExecutionState
    attempt: int = 1
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    result_hash: str = ""


def action_hash(task_id: str, tool: str, arguments: dict[str, Any]) -> str:
    payload = {"task_id": task_id, "tool": tool, "arguments": arguments}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ExecutionLedger:
    """One-row-per-action durable state machine."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS execution_ledger "
            "(action_hash TEXT PRIMARY KEY, task_id TEXT NOT NULL, tool TEXT NOT NULL, "
            "state TEXT NOT NULL, attempt INTEGER NOT NULL, created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, result_hash TEXT NOT NULL)"
        )

    def get(self, digest: str) -> ExecutionRecord | None:
        rows = self._db.query("SELECT action_hash,task_id,tool,state,attempt,created_at,updated_at,result_hash FROM execution_ledger WHERE action_hash=?", (digest,))
        if not rows:
            return None
        row = rows[0]
        return ExecutionRecord(action_hash=row[0], task_id=row[1], tool=row[2], state=ExecutionState(row[3]),
                               attempt=int(row[4]), created_at=datetime.fromisoformat(row[5]),
                               updated_at=datetime.fromisoformat(row[6]), result_hash=row[7])

    def begin(self, task_id: str, tool: str, arguments: dict[str, Any]) -> ExecutionRecord:
        digest = action_hash(task_id, tool, arguments)
        existing = self.get(digest)
        now = utcnow()
        if existing is not None:
            if existing.state in {ExecutionState.COMPLETED, ExecutionState.STARTED, ExecutionState.EXECUTING, ExecutionState.AUTHORIZED}:
                return existing
            attempt = existing.attempt + 1
            self._db.write("UPDATE execution_ledger SET state=?,attempt=?,updated_at=? WHERE action_hash=?",
                           (ExecutionState.STARTED.value, attempt, now.isoformat(), digest))
            return existing.model_copy(update={"state": ExecutionState.STARTED, "attempt": attempt, "updated_at": now})
        record = ExecutionRecord(action_hash=digest, task_id=task_id, tool=tool, state=ExecutionState.STARTED,
                                 created_at=now, updated_at=now)
        self._db.write("INSERT INTO execution_ledger VALUES (?,?,?,?,?,?,?,?)",
                       (record.action_hash, record.task_id, record.tool, record.state.value,
                        record.attempt, record.created_at.isoformat(), record.updated_at.isoformat(), ""))
        return record

    def transition(self, digest: str, state: ExecutionState, result_hash: str = "") -> None:
        now = utcnow()
        self._db.write("UPDATE execution_ledger SET state=?,updated_at=?,result_hash=? WHERE action_hash=?",
                       (state.value, now.isoformat(), result_hash, digest))

    def recover_unknowns(self) -> int:
        """Mark in-flight actions UNKNOWN after restart; never auto-retry side effects."""
        rows = self._db.query("SELECT action_hash FROM execution_ledger WHERE state IN ('STARTED','EXECUTING','AUTHORIZED')")
        for (digest,) in rows:
            self.transition(digest, ExecutionState.UNKNOWN)
        return len(rows)
