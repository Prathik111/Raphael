"""Human approvals bound to exact actions (review P0)."""

import threading
import time

import pytest

from ai_ecosystem.core.errors import DomainValidationError
from ai_ecosystem.core.models.enums import (
    ApprovalStatus,
    PermissionDecision,
    RiskLevel,
)
from ai_ecosystem.core.persistence import (
    SqliteApprovalRepository,
)
from ai_ecosystem.security import (
    ApprovalStore,
    AuthorizationManager,
    Policy,
    PolicyEngine,
    approval_hash,
)
from ai_ecosystem.tools import ToolRegistry, ToolRunner


def _registry(tmp_path):
    from ai_ecosystem.tools import filesystem_tools

    reg = ToolRegistry()
    for tool, handler in filesystem_tools(tmp_path):
        reg.register(tool, handler)
    return reg


def _floor_manager(registry, **kw):
    kw.setdefault("approval_store", ApprovalStore())
    policy = Policy(
        name="approval-test",
        approval_required_from=RiskLevel.HIGH,
        **{k: v for k, v in kw.items() if k in ("auto_grant_up_to", "deny_critical")},
    )
    store = kw["approval_store"]
    manager = AuthorizationManager(
        registry,
        policy_engine=PolicyEngine(policy),
        approval_store=store,
        approval_wait_s=kw.get("approval_wait_s", 30.0),
    )
    return manager, store


def test_hash_stable_and_sensitive():
    args = {"path": "a.txt", "mode": "r"}
    assert approval_hash("t", args, "p") == approval_hash("t", dict(args), "p")
    assert approval_hash("t", args, "p") != approval_hash("t", {"path": "b.txt", "mode": "r"}, "p")
    assert approval_hash("t", args, "p") != approval_hash("t", args, "other")
    assert approval_hash("t", args, "p") != approval_hash("u", args, "p")


def test_request_approve_verify_round_trip():
    store = ApprovalStore()
    created = store.request(
        "task-1", "filesystem.read", {"path": "a.txt"}, "LOW", "test", "default"
    )
    assert created.status is ApprovalStatus.PENDING
    # Same exact action reuses the request (idempotent).
    assert (
        store.request("task-1", "filesystem.read", {"path": "a.txt"}, "LOW", "test", "default").id
        == created.id
    )
    assert store.verify(created.id, "filesystem.read", {"path": "a.txt"}, "default") is False
    store.decide(created.id, True, decided_by="tester")
    assert store.verify(created.id, "filesystem.read", {"path": "a.txt"}, "default") is True


def test_approve_then_mutate_denied():
    store = ApprovalStore()
    created = store.request(
        "task-1", "filesystem.read", {"path": "a.txt"}, "LOW", "test", "default"
    )
    store.decide(created.id, True)
    assert store.verify(created.id, "filesystem.read", {"path": "b.txt"}, "default") is False
    assert store.verify(created.id, "filesystem.write", {"path": "a.txt"}, "default") is False
    assert store.verify(created.id, "filesystem.read", {"path": "a.txt"}, "other-policy") is False


def test_decide_once_and_unknown_rejected():
    store = ApprovalStore()
    created = store.request("t", "tool", {}, "LOW", "r", "p")
    store.decide(created.id, False)
    with pytest.raises(DomainValidationError):
        store.decide(created.id, True)
    with pytest.raises(DomainValidationError):
        store.decide("nope", True)


def test_expiry_sweeps_and_denies():
    store = ApprovalStore(default_ttl_s=0.05)
    created = store.request("t", "tool", {}, "LOW", "r", "p")
    time.sleep(0.1)
    assert store.sweep_expired() == 1
    assert store.get(created.id).status is ApprovalStatus.EXPIRED
    assert store.verify(created.id, "tool", {}, "p") is False
    with pytest.raises(DomainValidationError):
        store.decide(created.id, True)


def test_floor_off_creates_no_requests(tmp_path):
    registry = _registry(tmp_path)
    manager = AuthorizationManager(registry, approval_store=ApprovalStore())
    call = registry.build_call("t", "filesystem.read", {"path": "a.txt"})
    permission = manager.authorize("t", registry.get("filesystem.read"), call)
    assert permission.decision is PermissionDecision.GRANTED
    assert permission.approval_id == ""


def test_high_tool_waits_then_executes_on_approve(tmp_path):
    from ai_ecosystem.core.events import EventBus

    registry = _registry(tmp_path)
    (tmp_path / "a.txt").write_text("data")
    manager, store = _floor_manager(registry)
    runner = ToolRunner(registry, manager, EventBus())
    call = registry.build_call("t", "filesystem.read", {"path": "a.txt"})
    # filesystem.read is LOW: force the floor down to exercise the wait.
    manager._policy.policy.approval_required_from = RiskLevel.LOW
    box: dict = {}

    def work():
        box["result"] = runner.run(call)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    pending = []
    while time.monotonic() < deadline and not pending:
        pending = store.pending(task_id="t")
        time.sleep(0.05)
    assert len(pending) == 1
    store.decide(pending[0].id, True, decided_by="test")
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert box["result"].success is True
    assert box["result"].output == "data"


def test_deny_from_human_fails_closed(tmp_path):
    registry = _registry(tmp_path)
    manager, store = _floor_manager(registry)
    manager._policy.policy.approval_required_from = RiskLevel.LOW
    runner = ToolRunner(registry, manager)
    call = registry.build_call("t", "filesystem.read", {"path": "a.txt"})
    box: dict = {}
    thread = threading.Thread(
        target=lambda: box.setdefault("result", runner.run(call)), daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 10
    pending = []
    while time.monotonic() < deadline and not pending:
        pending = store.pending(task_id="t")
        time.sleep(0.05)
    store.decide(pending[0].id, False)
    thread.join(timeout=10)
    assert box["result"].success is False
    assert "denied" in box["result"].error.lower()


def test_wait_timeout_denies(tmp_path):
    registry = _registry(tmp_path)
    manager, store = _floor_manager(registry, approval_wait_s=1.0)
    manager._policy.policy.approval_required_from = RiskLevel.LOW
    runner = ToolRunner(registry, manager)
    started = time.monotonic()
    result = runner.run(registry.build_call("t", "filesystem.read", {"path": "a.txt"}))
    assert time.monotonic() - started < 10
    assert result.success is False
    assert "timed out" in result.error


def test_tampered_args_after_approve_denied(tmp_path):
    registry = _registry(tmp_path)
    manager, store = _floor_manager(registry, approval_wait_s=2.0)
    manager._policy.policy.approval_required_from = RiskLevel.LOW
    tool = registry.get("filesystem.read")
    call = registry.build_call("t", "filesystem.read", {"path": "a.txt"})
    permission = manager.authorize("t", tool, call)
    assert permission.decision is PermissionDecision.PENDING
    store.decide(permission.approval_id, True)
    # Re-authorizing the pristine call now grants via the approval.
    granted = manager.authorize("t", tool, call)
    assert granted.decision is PermissionDecision.GRANTED
    # Attacker swaps arguments between approval and execution: the
    # binding check fails closed even though an approval exists.
    call.arguments["path"] = "other.txt"
    assert manager.check_approval(granted, tool, call) is False
    # End to end, the tampered call can only open a NEW pending request,
    # which nobody approves: the runner times out denied, never executes.
    runner = ToolRunner(registry, manager)
    result = runner.run(call)
    assert result.success is False
    assert "denied" in result.error.lower()


def test_binding_recheck_message_on_race(tmp_path):
    """A grant whose arguments change mid-flight denies explicitly."""

    registry = _registry(tmp_path)
    manager, store = _floor_manager(registry)
    manager._policy.policy.approval_required_from = RiskLevel.LOW
    tool = registry.get("filesystem.read")
    call = registry.build_call("t", "filesystem.read", {"path": "a.txt"})
    permission = manager.authorize("t", tool, call)
    store.decide(permission.approval_id, True)
    granted = manager.authorize("t", tool, call)
    call.arguments["path"] = "other.txt"  # race: mutate after grant
    runner = ToolRunner(registry, manager)
    settled = runner._await_human(call, tool, granted)
    assert settled.decision is PermissionDecision.DENIED
    assert "does not match" in settled.reason


def test_approvals_persist_across_restart(tmp_path):
    from ai_ecosystem.interface import RuntimeAPI
    from ai_ecosystem.core.runtime import AgentRuntime

    path = str(tmp_path / "appr.db")
    runtime = AgentRuntime(path)
    try:
        store = ApprovalStore(SqliteApprovalRepository(runtime.db))
        created = store.request(
            "task-9", "filesystem.read", {"path": "a.txt"}, "HIGH", "ui", "default"
        )
        assert [r.id for r in store.pending()] == [created.id]
    finally:
        runtime.shutdown()
    reopened = AgentRuntime(path)
    try:
        store2 = ApprovalStore(SqliteApprovalRepository(reopened.db))
        pending = store2.pending()
        assert [r.id for r in pending] == [created.id]
        decided = store2.decide(created.id, True, decided_by="operator")
        assert decided.status is ApprovalStatus.APPROVED
        api = RuntimeAPI(reopened, approvals=store2)
        assert api.list_approvals() == []
        assert api.list_approvals(status="APPROVED")[0]["id"] == created.id
    finally:
        reopened.shutdown()


def test_approve_unknown_rejected_via_api(tmp_path):
    from ai_ecosystem.interface import RuntimeAPI
    from ai_ecosystem.core.runtime import AgentRuntime
    from ai_ecosystem.interface import ApiError

    runtime = AgentRuntime(str(tmp_path / "api.db"))
    try:
        api = RuntimeAPI(runtime, approvals=ApprovalStore())
        with pytest.raises(ApiError):
            api.decide_approval("missing", True)
        assert api.list_approvals() == []
    finally:
        runtime.shutdown()
