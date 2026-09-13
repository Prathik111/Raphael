from ai_ecosystem.core.persistence import Database
from ai_ecosystem.tools.registry.execution_ledger import (
    ExecutionLedger,
    ExecutionState,
    action_hash,
)


def test_action_hash_is_semantic_and_deterministic():
    a = action_hash("task", "tool", {"b": 2, "a": 1})
    b = action_hash("task", "tool", {"a": 1, "b": 2})
    assert a == b


def test_ledger_survives_restart_and_marks_inflight_unknown():
    db = Database(":memory:")
    db.migrate()
    ledger = ExecutionLedger(db)
    record = ledger.begin("task", "tool", {"path": "x"})
    assert record.state is ExecutionState.STARTED
    assert ledger.get(record.action_hash) is not None
    assert ledger.recover_unknowns() == 1
    assert ledger.get(record.action_hash).state is ExecutionState.UNKNOWN
    db.close()
