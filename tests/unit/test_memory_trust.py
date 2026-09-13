"""Regression tests for memory trust boundaries and expiration."""

from datetime import timedelta

import pytest

from ai_ecosystem.core.models.base import utcnow
from ai_ecosystem.core.models.enums import MemoryScope, MemoryType
from ai_ecosystem.core.persistence import Database, SqliteMemoryRepository
from ai_ecosystem.personalization.memory import MemoryCandidate, MemoryStore, MemoryVerifier


def _store():
    db = Database(":memory:")
    db.migrate()
    return db, MemoryStore(SqliteMemoryRepository(db), database=db)


def test_memory_preserves_provenance_and_defaults_to_unverified():
    db, store = _store()
    try:
        memory = store.store(
            MemoryCandidate(
                content="Historical task completed.",
                type=MemoryType.EPISODIC,
                source="task:test-1",
                confidence=0.9,
                importance=0.9,
                scope=MemoryScope.GLOBAL,
                metadata={"provenance": "task_result", "created_by": "agent", "verified": True},
            )
        )
        assert memory.provenance == "task_result"
        assert memory.created_by == "agent"
        assert memory.verified is False
    finally:
        db.close()


def test_only_matching_verification_receipt_can_promote_trust():
    db, store = _store()
    try:
        candidate = MemoryCandidate(content="Observed result", confidence=0.9, importance=0.9)
        receipt = MemoryVerifier().verify(
            candidate, evidence_ids=["event:1"], verifier="system-verifier", method="observed-state"
        )
        assert store.store_verified(candidate, receipt).verified is True
        changed = candidate.model_copy(update={"content": "Tampered result"})
        with pytest.raises(Exception):
            store.store_verified(changed, receipt)
    finally:
        db.close()


def test_expired_memory_is_not_recalled_and_can_be_purged():
    db, store = _store()
    try:
        memory = store.store(
            MemoryCandidate(
                content="Expired historical instruction.",
                confidence=0.9,
                importance=0.9,
                metadata={"expires_at": utcnow() - timedelta(seconds=1)},
            )
        )
        assert store.retrieve(MemoryScope.GLOBAL, query="expired historical") == []
        assert memory.id in store.purge_expired()
    finally:
        db.close()
