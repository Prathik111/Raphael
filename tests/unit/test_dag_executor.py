"""Gate 8: DAG executor -- ordering, parallelism, limits, failure, cancel, timeout."""

import threading
import time

import pytest

from ai_ecosystem.agent import (
    CancellationToken,
    FailurePolicy,
    GraphNode,
    OverallStatus,
    ParallelExecutor,
    TaskGraph,
)
from ai_ecosystem.core.errors import DomainValidationError, PlanValidationError
from ai_ecosystem.core.events import Event, EventBus
from ai_ecosystem.core.models import Plan, PlanStep, Tool, ToolResult
from ai_ecosystem.core.models.enums import EventType, StepState
from ai_ecosystem.tools import (
    DenyAllAuthorizer,
    GrantAllAuthorizer,
    ToolRegistry,
    ToolRunner,
)


def _plan(*items):
    """items: (id, deps, tools)."""
    steps = [
        PlanStep(
            id=sid,
            description=f"step {sid}",
            dependencies=list(deps),
            tools=list(tools),
            verification="checked",
            completion_criteria="done",
        )
        for sid, deps, tools in items
    ]
    return Plan(goal="g", steps=steps, final_verification="v")


def _registry(**handlers):
    reg = ToolRegistry()
    for name, fn in handlers.items():
        reg.register(Tool(name=name, input_schema={"required": []}), fn)
    return reg


def _ok(args):
    return ToolResult(success=True, output=args.get("v", "ok"))


def _harness(registry, bus=None, **kwargs):
    bus = bus or EventBus()
    runner = ToolRunner(registry, GrantAllAuthorizer(), bus)
    executor = ParallelExecutor(runner, registry, bus, **kwargs)
    return executor, bus


def test_sequential_dependency_order():
    order, lock = [], threading.Lock()
    registry = _registry(
        ok=lambda args: (lock.acquire(), order.append(args["v"]), lock.release()) and _ok(args)
    )
    executor, _ = _harness(registry, max_concurrency=4)
    plan = _plan(("a", [], ["ok"]), ("b", ["a"], ["ok"]), ("c", ["b"], ["ok"]))
    result = executor.execute(
        "t", plan, arguments={"a": {"v": "a"}, "b": {"v": "b"}, "c": {"v": "c"}}
    )
    assert result.status is OverallStatus.COMPLETED
    assert order == ["a", "b", "c"]
    assert result.succeeded == ["a", "b", "c"]


def test_parallel_branches_overlap():
    barrier = threading.Barrier(2, timeout=5)

    def rendezvous(args):
        barrier.wait()
        return ToolResult(success=True, output="met")

    registry = _registry(ok=_ok, meet=rendezvous)
    executor, _ = _harness(registry, max_concurrency=2)
    plan = _plan(("a", [], ["ok"]), ("b", ["a"], ["meet"]), ("c", ["a"], ["meet"]))
    result = executor.execute("t", plan)
    assert result.status is OverallStatus.COMPLETED
    assert set(result.succeeded) == {"a", "b", "c"}


def test_fan_in_waits_for_all_branches():
    log, lock = [], threading.Lock()

    def traced(args):
        name = args["v"]
        with lock:
            log.append(("start", name, time.monotonic()))
        time.sleep(0.05)
        with lock:
            log.append(("end", name, time.monotonic()))
        return ToolResult(success=True, output=name)

    registry = _registry(ok=_ok, traced=traced)
    executor, _ = _harness(registry, max_concurrency=4)
    plan = _plan(
        ("a", [], ["ok"]),
        ("b", ["a"], ["traced"]),
        ("c", ["a"], ["traced"]),
        ("d", ["b", "c"], ["traced"]),
    )
    arguments = {"b": {"v": "b"}, "c": {"v": "c"}, "d": {"v": "d"}}
    result = executor.execute("t", plan, arguments=arguments)
    assert result.status is OverallStatus.COMPLETED
    starts = {name: ts for kind, name, ts in log if kind == "start"}
    ends = {name: ts for kind, name, ts in log if kind == "end"}
    assert starts["d"] > max(ends["b"], ends["c"])


def test_concurrency_limit_enforced():
    state = {"active": 0, "max": 0}
    lock = threading.Lock()

    def counted(args):
        with lock:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        time.sleep(0.03)
        with lock:
            state["active"] -= 1
        return ToolResult(success=True, output="x")

    registry = _registry(work=counted)
    executor, _ = _harness(registry, max_concurrency=3)
    plan = _plan(*[(f"s{i}", [], ["work"]) for i in range(10)])
    result = executor.execute("t", plan)
    assert result.status is OverallStatus.COMPLETED
    assert state["max"] <= 3
    assert state["max"] > 1  # genuinely parallel, not serialized


def _fail(args):
    from ai_ecosystem.core.errors import ToolExecutionError

    raise ToolExecutionError("stub.fail", "injected failure")


def test_failure_fail_fast():
    registry = _registry(ok=_ok, fail=_fail)
    # max_concurrency=1: c is still PENDING when b fails, so the halt
    # provably stops unstarted work (a concurrent c could not be un-started).
    executor, bus = _harness(registry, max_concurrency=1, failure_policy=FailurePolicy.FAIL_FAST)
    plan = _plan(
        ("a", [], ["ok"]),
        ("b", ["a"], ["fail"]),
        ("c", ["a"], ["ok"]),
        ("d", ["b", "c"], ["ok"]),
    )
    result = executor.execute("t", plan)
    assert result.status is OverallStatus.FAILED
    assert result.failed == ["b"]
    assert result.skipped == ["d"]  # dependency failed: never runnable
    assert result.cancelled == ["c"]  # independent: halted, never started


def test_failure_continue_independent():
    registry = _registry(ok=_ok, fail=_fail)
    executor, _ = _harness(registry, failure_policy=FailurePolicy.CONTINUE_INDEPENDENT)
    plan = _plan(
        ("a", [], ["ok"]),
        ("b", ["a"], ["fail"]),
        ("c", ["a"], ["ok"]),
        ("d", ["b", "c"], ["ok"]),
    )
    result = executor.execute("t", plan)
    assert result.status is OverallStatus.FAILED
    assert result.failed == ["b"]
    assert result.succeeded == ["a", "c"]
    assert result.skipped == ["d"]


def test_cancellation_stops_unstarted_work():
    started = threading.Event()

    def slow(args):
        started.set()
        time.sleep(0.5)
        return ToolResult(success=True, output="slow")

    registry = _registry(slow=slow)
    bus = EventBus()
    runner = ToolRunner(registry, GrantAllAuthorizer(), bus)
    executor = ParallelExecutor(runner, registry, bus, max_concurrency=1)
    graph = TaskGraph.from_plan(
        _plan(*[(f"s{i}", [f"s{i - 1}"] if i else [], ["slow"]) for i in range(4)])
    )
    token = CancellationToken()
    outcome = {}

    def drive():
        outcome["result"] = executor.execute_graph("t", graph, cancel=token)

    thread = threading.Thread(target=drive)
    thread.start()
    assert started.wait(timeout=5)
    token.cancel()
    thread.join(timeout=10)
    assert not thread.is_alive()
    result = outcome["result"]
    assert result.status is OverallStatus.CANCELLED
    by_id = {node.step.id: node for node in graph.nodes}
    assert by_id["s0"].attempts == 1  # was running; ran to natural outcome
    for sid in ("s1", "s2", "s3"):
        assert by_id[sid].attempts == 0  # never started after cancel
        assert by_id[sid].state is StepState.CANCELLED


def test_pre_cancelled_token_runs_nothing():
    registry = _registry(ok=_ok)
    executor, _ = _harness(registry)
    token = CancellationToken()
    token.cancel()
    result = executor.execute("t", _plan(("a", [], ["ok"])), cancel=token)
    assert result.status is OverallStatus.CANCELLED
    assert result.cancelled == ["a"]


def test_step_timeout_marks_timed_out():
    def slow(args):
        time.sleep(3)
        return ToolResult(success=True, output="late")

    registry = _registry(slow=slow)
    executor, _ = _harness(registry, default_step_timeout_s=0.2)
    result = executor.execute("t", _plan(("a", [], ["slow"])))
    assert result.status is OverallStatus.FAILED
    assert result.timed_out == ["a"]
    assert "deadline" in result.errors[0]


def test_invalid_graph_refused():
    registry = _registry(ok=_ok)
    executor, _ = _harness(registry)
    with pytest.raises(PlanValidationError, match="unknown step"):
        executor.execute("t", _plan(("a", ["ghost"], ["ok"])))
    cyclic = Plan(
        goal="g",
        steps=[
            PlanStep(
                id="a",
                description="a",
                dependencies=["b"],
                tools=["ok"],
                verification="v",
                completion_criteria="c",
            ),
            PlanStep(
                id="b",
                description="b",
                dependencies=["a"],
                tools=["ok"],
                verification="v",
                completion_criteria="c",
            ),
        ],
        final_verification="v",
    )
    with pytest.raises(PlanValidationError, match="cycle"):
        executor.execute("t", cyclic)


def test_denied_tool_never_runs_handler():
    executed = []
    registry = ToolRegistry()
    registry.register(
        Tool(name="evil", input_schema={"required": []}),
        lambda args: executed.append(True),
    )
    bus = EventBus()
    runner = ToolRunner(registry, DenyAllAuthorizer(), bus)
    executor = ParallelExecutor(runner, registry, bus)
    result = executor.execute("t", _plan(("a", [], ["evil"])))
    assert result.status is OverallStatus.FAILED
    assert executed == []
    assert result.failed == ["a"]


def test_step_state_transitions_validated():
    node = GraphNode(
        step=PlanStep(
            id="a", description="a", tools=["ok"], verification="v", completion_criteria="c"
        )
    )
    with pytest.raises(DomainValidationError):
        node.transition(StepState.SUCCEEDED)  # PENDING -> SUCCEEDED illegal
    node.transition(StepState.READY)
    node.transition(StepState.RUNNING)
    node.transition(StepState.SUCCEEDED)
    with pytest.raises(DomainValidationError):
        node.transition(StepState.RUNNING)  # terminal: no outgoing


def test_result_aggregation_shape():
    registry = _registry(ok=_ok, fail=_fail)
    executor, _ = _harness(registry, failure_policy=FailurePolicy.CONTINUE_INDEPENDENT)
    plan = _plan(("a", [], ["ok"]), ("b", ["a"], ["fail"]))
    result = executor.execute("t", plan)
    assert result.task_id == "t"
    assert result.states == {"a": StepState.SUCCEEDED, "b": StepState.FAILED}
    assert result.duration_s >= 0
    assert any("b:" in error for error in result.errors)


def test_lifecycle_events_emitted():
    registry = _registry(ok=_ok)
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe_all(seen.append)
    runner = ToolRunner(registry, GrantAllAuthorizer(), bus)
    executor = ParallelExecutor(runner, registry, bus)
    executor.execute("t1", _plan(("a", [], ["ok"]), ("b", ["a"], ["ok"])))
    kinds = [e.event_type for e in seen]
    assert kinds[0] is EventType.GRAPH_STARTED
    assert kinds[-1] is EventType.GRAPH_COMPLETED
    assert kinds.count(EventType.STEP_STARTED) == 2
    assert kinds.count(EventType.STEP_COMPLETED) == 2
    assert all(e.task_id == "t1" for e in seen)


def test_race_stability_over_100_iterations():
    registry = _registry(ok=_ok)
    plan = _plan(
        ("a", [], ["ok"]),
        ("b", ["a"], ["ok"]),
        ("c", ["a"], ["ok"]),
        ("d", ["b", "c"], ["ok"]),
    )
    for i in range(100):
        executor, _ = _harness(registry, max_concurrency=4)
        result = executor.execute(f"t{i}", plan)
        assert result.status is OverallStatus.COMPLETED, f"iteration {i}"
        assert set(result.succeeded) == {"a", "b", "c", "d"}
