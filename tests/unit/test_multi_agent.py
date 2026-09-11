"""Gate 18: many agents, one shared runtime, isolated permissions."""

import threading
import time

import pytest

from ai_ecosystem.agent.executor import OverallStatus
from ai_ecosystem.agent.multi import (
    AgentDefinition,
    AgentManager,
    AgentRegistry,
    AgentStatus,
    SubtaskSpec,
    Supervisor,
)
from ai_ecosystem.agent.verifier import Verifier
from ai_ecosystem.core.errors import DomainValidationError
from ai_ecosystem.core.models import Plan, PlanStep, Tool, ToolResult
from ai_ecosystem.core.models.enums import EventType, RiskLevel
from ai_ecosystem.core.persistence import Database
from ai_ecosystem.core.runtime import AgentRuntime
from ai_ecosystem.tools import ToolRegistry


def _ok(args):
    return ToolResult(success=True, output="ok")


def _step(sid, tool, deps=()):
    return PlanStep(
        id=sid,
        description=sid,
        dependencies=list(deps),
        tools=[tool],
        verification="v",
        completion_criteria="c",
    )


def _plan(tool, sid="s1"):
    return Plan(goal="g", steps=[_step(sid, tool)], final_verification="v")


@pytest.fixture()
def world(tmp_path):
    path = str(tmp_path / "multi.db")
    db = Database(path)
    db.migrate()
    runtime = AgentRuntime(path)
    bus = runtime.bus
    registry = ToolRegistry()
    registry.register(
        Tool(name="research", input_schema={"required": []}, risk_level=RiskLevel.LOW), _ok
    )
    registry.register(
        Tool(name="code", input_schema={"required": []}, risk_level=RiskLevel.LOW), _ok
    )
    registry.register(
        Tool(name="inspect", input_schema={"required": []}, risk_level=RiskLevel.LOW), _ok
    )
    calls = {"danger": 0}

    def danger(args):
        calls["danger"] += 1
        return ToolResult(success=True, output="pwned")

    registry.register(
        Tool(name="danger", input_schema={"required": []}, risk_level=RiskLevel.HIGH), danger
    )
    agents = AgentRegistry(db, bus)
    defs = {}
    for name, role, tools in (
        ("r1", "research", ["research"]),
        ("c1", "coding", ["code"]),
        ("s1", "system", ["inspect"]),
    ):
        created = agents.register(
            AgentDefinition(
                id=f"{name}-id",
                name=name,
                role=role,
                capabilities=[role, "analysis"],
                allowed_tools=list(tools),
            )
        )
        defs[name] = created
    manager = AgentManager(agents, runtime, registry, max_workers=2, bus=bus)
    for agent_id in ("r1-id", "c1-id", "s1-id"):
        manager.spawn(agent_id)
    yield {
        "db": db,
        "runtime": runtime,
        "bus": bus,
        "registry": registry,
        "agents": agents,
        "manager": manager,
        "defs": defs,
        "calls": calls,
        "path": path,
    }
    runtime.shutdown()
    db.close()


def test_1_agent_registration(world):
    assert world["agents"].lookup("r1-id").role == "research"


def test_2_duplicate_rejection(world):
    with pytest.raises(DomainValidationError, match="duplicate"):
        world["agents"].register(AgentDefinition(id="r1-id", name="clone"))


def test_3_agent_lookup(world):
    assert world["agents"].lookup("nope") is None
    assert [d.name for d in world["agents"].by_role("coding")] == ["c1"]
    assert len(world["agents"].list()) == 3


def test_4_scoped_capabilities(world):
    definition = world["agents"].lookup("r1-id")
    assert definition.allowed_tools == ["research"]
    assert definition.risk_profile is RiskLevel.MEDIUM


def test_5_agent_lifecycle(world):
    manager = world["manager"]
    assert world["agents"].lookup("r1-id").status is AgentStatus.READY
    record = manager.submit("r1-id", "look things up", _plan("research"))
    assert record.status is AgentStatus.CREATED
    manager.run_task(record.id)
    stored = manager.task_repository.get(record.id)
    assert stored.status is AgentStatus.COMPLETED
    assert world["agents"].lookup("r1-id").status is AgentStatus.READY


def test_6_supervisor_delegation(world):
    supervisor = Supervisor(world["manager"])
    specs = [
        SubtaskSpec("r1-id", "find facts", _plan("research")),
        SubtaskSpec("c1-id", "write code", _plan("code")),
    ]
    outcome = supervisor.run_goal("ship it", specs)
    assert len(outcome.task_ids) == 2
    assert len(outcome.succeeded) == 2
    assert outcome.failed == []


def test_7_child_task_creation(world):
    manager = world["manager"]
    record = manager.submit(
        "c1-id", "sub work", _plan("code"), parent_agent="supervisor", parent_task="root-1"
    )
    assert record.parent_agent == "supervisor"
    assert record.parent_task == "root-1"


def test_8_task_ownership(world):
    manager = world["manager"]
    record = manager.submit("s1-id", "check disk", _plan("inspect"))
    fetched = manager.task_repository.by_task(record.task_id)
    assert fetched is not None
    assert fetched.owner_agent == "s1-id"
    assert fetched.id == record.id


def test_9_parallel_agent_execution(world):
    barrier = threading.Barrier(2, timeout=5)

    def meet(args):
        barrier.wait()
        return ToolResult(success=True, output="met")

    world["registry"].register(Tool(name="meet", input_schema={"required": []}), meet)
    # Widen both allow-lists through the persisted definitions.
    from ai_ecosystem.core.persistence.sqlite import _SnapshotTable

    table = _SnapshotTable(world["db"], "agent_definitions", AgentDefinition)
    for agent_id in ("r1-id", "c1-id"):
        definition = table.get(agent_id)
        definition.allowed_tools = ["meet"]
        table.update(definition)
    manager = world["manager"]
    plan = Plan(
        goal="g",
        steps=[
            PlanStep(
                id="s1",
                description="meet",
                dependencies=[],
                tools=["meet"],
                verification="v",
                completion_criteria="c",
            )
        ],
        final_verification="v",
    )
    r1 = manager.submit("r1-id", "meet up", plan)
    r2 = manager.submit("c1-id", "meet up", plan)
    results = manager.run_all([r1.id, r2.id])
    assert len(results) == 2
    assert all(r.status is OverallStatus.COMPLETED for r in results.values())


def test_10_concurrency_limits():
    path = ":memory:"
    db = Database(path)
    db.migrate()
    runtime = AgentRuntime(path)
    registry = ToolRegistry()
    active = {"current": 0, "max": 0}
    lock = threading.Lock()

    def slow(args):
        with lock:
            active["current"] += 1
            active["max"] = max(active["max"], active["current"])
        time.sleep(0.05)
        with lock:
            active["current"] -= 1
        return ToolResult(success=True, output="slow")

    registry.register(Tool(name="slow", input_schema={"required": []}), slow)
    agents = AgentRegistry(db)
    for index in range(3):
        agents.register(AgentDefinition(id=f"a{index}", name=f"a{index}", allowed_tools=["slow"]))
    manager = AgentManager(agents, runtime, registry, max_workers=1)
    for index in range(3):
        manager.spawn(f"a{index}")
    records = [manager.submit(f"a{index}", "go slow", _plan("slow")) for index in range(3)]
    try:
        results = manager.run_all([r.id for r in records])
    finally:
        runtime.shutdown()
        db.close()
    assert len(results) == 3
    assert active["max"] == 1  # strictly sequential under max_workers=1


def test_11_child_permission_isolation(world):
    manager = world["manager"]
    record = manager.submit("r1-id", "be evil", _plan("danger"))
    result = manager.run_task(record.id)
    assert result.status is OverallStatus.FAILED
    assert world["calls"]["danger"] == 0  # research agent cannot run it


def test_12_unauthorized_escalation_blocked(world):
    manager = world["manager"]
    first, _ = manager.scoped_runner("r1-id")
    second, _ = manager.scoped_runner("c1-id")
    assert first is not second
    # Distinct scoped policies: research allow-list lacks coding tools.
    call = world["registry"].build_call("t", "code", {})
    permission = second._authorizer.authorize("t", world["registry"].get("code"), call)
    assert permission.decision.value == "GRANTED"
    denied = first._authorizer.authorize(
        "t", world["registry"].get("code"), world["registry"].build_call("t", "code", {})
    )
    assert denied.decision.value == "DENIED"


def test_13_agent_failure_propagation(world):
    def fail(args):
        from ai_ecosystem.core.errors import ToolExecutionError

        raise ToolExecutionError("code", "cannot compile")

    world["registry"].register(Tool(name="broken", input_schema={"required": []}), fail)
    definition = world["agents"].lookup("c1-id")
    definition.allowed_tools = ["code", "broken"]
    from ai_ecosystem.core.persistence.sqlite import _SnapshotTable

    table = _SnapshotTable(world["db"], "agent_definitions", AgentDefinition)
    table.update(definition)
    supervisor = Supervisor(world["manager"])
    bad = Plan(goal="g", steps=[_step("s1", "broken")], final_verification="v")
    outcome = supervisor.run_goal(
        "mixed",
        [SubtaskSpec("r1-id", "fine", _plan("research")), SubtaskSpec("c1-id", "broken", bad)],
    )
    assert len(outcome.succeeded) == 1
    assert len(outcome.failed) == 1


def test_14_cancellation(world):
    manager = world["manager"]
    from ai_ecosystem.agent.executor import CancellationToken

    records = [manager.submit("r1-id", f"job {i}", _plan("research")) for i in range(3)]
    token = CancellationToken()
    token.cancel()
    results = manager.run_all([r.id for r in records], token)
    assert results == {}
    for record in records:
        assert manager.task_repository.get(record.id).status is AgentStatus.CREATED


def test_15_restart_recovery(world):
    manager = world["manager"]
    record = manager.submit("r1-id", "half done", _plan("research"))
    stored = manager.task_repository.get(record.id)
    stored.status = AgentStatus.RUNNING  # simulate kill mid-run
    manager.task_repository.update(stored)
    path = world["path"]
    world["runtime"].shutdown()
    world["db"].close()
    db = Database(path)
    db.migrate()
    runtime = AgentRuntime(path)
    try:
        fresh = AgentManager(AgentRegistry(db), runtime, world["registry"])
        reset = fresh.resume_interrupted()
        assert [r.id for r in reset] == [record.id]
        assert fresh.task_repository.get(record.id).status is AgentStatus.READY
        assert fresh.task_repository.get(record.id).interrupted is True
        assert fresh._agents.lookup("r1-id").role == "research"  # definitions persist
        result = fresh.run_task(record.id)  # re-runnable after resume
        assert result.status is OverallStatus.COMPLETED
    finally:
        runtime.shutdown()
        db.close()


def test_16_shared_runtime_reuse(world):
    manager = world["manager"]
    r1 = manager.submit("r1-id", "one", _plan("research"))
    r2 = manager.submit("c1-id", "two", _plan("code"))
    manager.run_all([r1.id, r2.id])
    tasks = world["runtime"].manager._tasks.list()
    ids = {t.id for t in tasks}
    assert {r1.task_id, r2.task_id} <= ids  # one runtime, both tasks
    assert manager.task_repository.by_task(r1.task_id).owner_agent == "r1-id"
    assert manager.task_repository.by_task(r2.task_id).owner_agent == "c1-id"


def test_17_deterministic_scheduling(world):
    supervisor = Supervisor(world["manager"])

    def attempt():
        specs = [
            SubtaskSpec("r1-id", "a", _plan("research")),
            SubtaskSpec("c1-id", "b", _plan("code")),
        ]
        outcome = supervisor.run_goal("go", specs)
        owners = sorted(
            world["manager"].task_repository.by_task(tid).owner_agent for tid in outcome.task_ids
        )
        return owners, sorted(outcome.succeeded) == sorted(outcome.task_ids)

    first, second = attempt(), attempt()
    assert first == second == (["c1-id", "r1-id"], True)


def test_supervisor_verifies_child_results(world):
    supervisor = Supervisor(world["manager"], verifier=Verifier())
    outcome = supervisor.run_goal("go", [SubtaskSpec("r1-id", "a", _plan("research"))])
    verdicts = supervisor.verify_results(outcome)
    assert len(verdicts) == 1
    from ai_ecosystem.core.models.enums import VerificationStatus

    assert next(iter(verdicts.values())).status is VerificationStatus.PASSED


def test_agent_events(world):
    assert world["agents"].lookup("s1-id").status is AgentStatus.READY
    seen = []
    world["bus"].subscribe_all(seen.append)
    record = world["manager"].submit("s1-id", "check", _plan("inspect"))
    world["manager"].run_task(record.id)
    kinds = [e.event_type for e in seen]
    assert EventType.AGENT_STARTED in kinds
    assert EventType.AGENT_COMPLETED in kinds
    assert EventType.AGENT_REGISTERED not in kinds  # registration predates subscription


def test_agent_lookup_perf_smoke(world):
    started = time.monotonic()
    for _ in range(500):
        world["agents"].lookup("r1-id")
        world["agents"].by_role("coding")
    elapsed = time.monotonic() - started
    print(f"\nagent smoke: 1000 lookups in {elapsed:.2f}s")
    assert elapsed < 5


def test_multi_agent_scheduling_perf_smoke(world):
    manager = world["manager"]
    records = [manager.submit("r1-id", f"job {i}", _plan("research")) for i in range(6)]
    started = time.monotonic()
    results = manager.run_all([r.id for r in records])
    elapsed = time.monotonic() - started
    print(f"\nscheduling smoke: 6 agent tasks in {elapsed:.2f}s")
    assert len(results) == 6
    # Smoke bound only guards pathology (deadlock/hang), not speed:
    # full-suite CPU contention can stretch trivial tasks.
    assert elapsed < 30
