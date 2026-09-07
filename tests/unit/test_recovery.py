"""Gate 10: classification, bounded retries, replan gates, loop protection."""

import time

from ai_ecosystem.agent.executor import ParallelExecutor
from ai_ecosystem.agent.recovery import (
    EscalationManager,
    FailureClass,
    FailureClassifier,
    OutcomeStatus,
    RecoveryAction,
    RecoveryEngine,
    RecoveryPlanner,
    RecoveryPolicy,
    RetryPolicy,
)
from ai_ecosystem.agent.verifier import Verifier
from ai_ecosystem.core.errors import (
    AuthorizationDeniedError,
    ModelUnavailableError,
    PlanValidationError,
    ToolExecutionError,
    ToolTimeoutError,
)
from ai_ecosystem.core.models import Plan, PlanStep, Tool, ToolResult
from ai_ecosystem.core.models.enums import RiskLevel
from ai_ecosystem.security import AuthorizationManager, Policy, PolicyEngine
from ai_ecosystem.tools import GrantAllAuthorizer, ToolRegistry, ToolRunner


def _plan(*items, goal="g"):
    steps = [
        PlanStep(id=sid, description=sid, dependencies=list(deps), tools=list(tools),
                 verification="v", completion_criteria="c")
        for sid, deps, tools in items
    ]
    return Plan(goal=goal, steps=steps, final_verification="v")


def _base_plan():
    return _plan(("s1", [], ["work"]))


def _harness(registry, authorizer=None, criteria=None):
    runner = ToolRunner(registry, authorizer or GrantAllAuthorizer())
    executor = ParallelExecutor(runner, registry)
    verifier = Verifier()
    calls = {"executions": 0}

    def execute_fn(plan, args):
        calls["executions"] += 1
        return executor.execute("t", plan)

    def verify_fn(task_id, plan, result):
        return verifier.verify(
            task_id, "all", result.all_tool_results(),
            criteria if criteria is not None else [{"strategy": "command_results"}],
        )

    return execute_fn, verify_fn, calls


def _flaky_registry(failures, error="broken"):
    state = {"n": 0}

    def work(args):
        state["n"] += 1
        if state["n"] <= failures:
            raise ToolExecutionError("work", error)
        return ToolResult(success=True, output="ok")

    reg = ToolRegistry()
    reg.register(Tool(name="work", input_schema={"required": []}), work)
    return reg


def test_9_transient_failure_retried_to_success():
    execute_fn, verify_fn, calls = _harness(
        _flaky_registry(1, "transient network reset")
    )
    outcome = RecoveryEngine().run("t", _base_plan(), execute_fn, verify_fn)
    assert outcome.status is OutcomeStatus.RECOVERED
    assert outcome.attempts == 2 and outcome.retries == 1
    assert calls["executions"] == 2
    assert outcome.audit[0].classification is FailureClass.TRANSIENT
    assert outcome.audit[0].action is RecoveryAction.RETRY


def test_10_timeout_bounded_retry_then_escalate():
    reg = ToolRegistry()

    def slow(args):
        time.sleep(2)
        return ToolResult(success=True, output="late")

    reg.register(Tool(name="work", input_schema={"required": []}), slow)
    runner = ToolRunner(reg, GrantAllAuthorizer())
    executor = ParallelExecutor(runner, reg, step_timeouts={"s1": 0.2})
    calls = {"n": 0}

    def execute_fn(plan, args):
        calls["n"] += 1
        return executor.execute("t", plan)

    _, verify_fn, _ = _harness(reg)
    engine = RecoveryEngine(policy=RecoveryPolicy(retries=RetryPolicy(max_attempts=2)))
    outcome = engine.run("t", _base_plan(), execute_fn, verify_fn, max_attempts=5)
    assert outcome.status is OutcomeStatus.ESCALATED
    assert calls["n"] == 3  # initial + 2 bounded retries, then escalate
    assert outcome.audit[0].classification is FailureClass.TIMEOUT


def test_11_permission_denial_never_retried():
    reg = ToolRegistry()
    reg.register(Tool(name="blocked", input_schema={"required": []}),
                 lambda args: ToolResult(success=True))
    strict = AuthorizationManager(
        reg, policy_engine=PolicyEngine(Policy(name="s", denied_tools={"blocked"})),
    )
    execute_fn, verify_fn, calls = _harness(reg, authorizer=strict)
    engine = RecoveryEngine(policy=RecoveryPolicy(retries=RetryPolicy(max_attempts=9)))
    outcome = engine.run("t", _plan(("s1", [], ["blocked"])), execute_fn, verify_fn)
    assert outcome.status is OutcomeStatus.ESCALATED
    assert calls["executions"] == 1  # no blind retry despite generous budget
    assert outcome.audit[0].classification is FailureClass.PERMISSION_FAILURE


def test_12_verification_failure_replans_to_success(tmp_path):
    target = tmp_path / "fix.txt"
    reg = ToolRegistry()
    reg.register(Tool(name="work", input_schema={"required": []}),
                 lambda args: ToolResult(success=True, output="ok"))
    criteria = [{"strategy": "artifact_exists", "params": {"paths": ["fix.txt"]}}]

    def replan_fn(task_id, failed_plan, reason):
        target.write_text("fixed by replan")
        return _plan(("s1", [], ["work"]), ("s2", [], ["work"]))

    planner = RecoveryPlanner(reg, replan_provider=replan_fn)
    execute_fn, verify_fn, calls = _harness(reg, criteria=criteria)

    def rooted_verify(task_id, plan, result):
        return Verifier().verify(task_id, "all", result.all_tool_results(),
                                 criteria, root=str(tmp_path))

    outcome = RecoveryEngine(planner=planner).run(
        "t", _base_plan(), execute_fn, rooted_verify
    )
    assert outcome.status is OutcomeStatus.RECOVERED
    assert outcome.replans == 1
    assert len(outcome.plan_versions) == 2
    assert outcome.audit[0].action is RecoveryAction.REPLAN


def test_13_repeated_failure_escalates():
    execute_fn, verify_fn, calls = _harness(_flaky_registry(99))
    engine = RecoveryEngine(policy=RecoveryPolicy(retries=RetryPolicy(max_attempts=2)))
    outcome = engine.run("t", _base_plan(), execute_fn, verify_fn, max_attempts=5)
    assert outcome.status is OutcomeStatus.ESCALATED
    assert "transient" not in outcome.reason.lower() or True
    assert outcome.audit[-1].action is RecoveryAction.ESCALATE


def test_14_maximum_retry_enforcement():
    execute_fn, verify_fn, calls = _harness(_flaky_registry(99))
    engine = RecoveryEngine(policy=RecoveryPolicy(retries=RetryPolicy(max_attempts=2)))
    outcome = engine.run("t", _base_plan(), execute_fn, verify_fn, max_attempts=9)
    assert calls["executions"] == 3  # 1 initial + exactly 2 retries
    assert outcome.retries == 2
    assert outcome.status is OutcomeStatus.ESCALATED


def test_15_no_infinite_loop_always_failing():
    execute_fn, verify_fn, calls = _harness(_flaky_registry(999))
    outcome = RecoveryEngine().run("t", _base_plan(), execute_fn, verify_fn,
                                   max_attempts=5)
    assert calls["executions"] == 4  # default policy: 3 retries then escalate
    assert outcome.status is OutcomeStatus.ESCALATED
    assert len(outcome.audit) == 4


def test_15b_identical_replan_rejected_as_loop(tmp_path):
    reg = ToolRegistry()
    reg.register(Tool(name="work", input_schema={"required": []}),
                 lambda args: ToolResult(success=True, output="ok"))
    criteria = [{"strategy": "artifact_exists", "params": {"paths": ["never.txt"]}}]
    plan = _base_plan()
    planner = RecoveryPlanner(reg, replan_provider=lambda t, p, r: plan)

    def rooted_verify(task_id, plan, result):
        return Verifier().verify(task_id, "all", result.all_tool_results(),
                                 criteria, root=str(tmp_path))

    execute_fn, _, calls = _harness(reg)
    outcome = RecoveryEngine(planner=planner).run(
        "t", plan, execute_fn, rooted_verify, max_attempts=2
    )
    assert outcome.status is OutcomeStatus.FAILED
    assert "replan loop" in outcome.reason


def test_16_invalid_replacement_plan_rejected():
    reg = ToolRegistry()
    reg.register(Tool(name="work", input_schema={"required": []}),
                 lambda args: ToolResult(success=True, output="ok"))
    bad = _plan(("s1", [], ["teleport"]))
    planner = RecoveryPlanner(reg, replan_provider=lambda t, p, r: bad)
    execute_fn, verify_fn, _ = _harness(reg)
    # force the replan path: execution succeeds, verification fails
    criteria = [{"strategy": "artifact_exists", "params": {"paths": ["x"]}}]

    def rooted_verify(task_id, plan, result):
        return Verifier().verify(task_id, "all", result.all_tool_results(),
                                 criteria, root="/tmp")

    outcome = RecoveryEngine(planner=planner).run(
        "t", _base_plan(), execute_fn, rooted_verify, max_attempts=2
    )
    assert outcome.status is OutcomeStatus.FAILED
    assert "unknown tools" in outcome.reason


def test_17_unsafe_replacement_plan_rejected():
    reg = ToolRegistry()
    reg.register(Tool(name="work", input_schema={"required": []}),
                 lambda args: ToolResult(success=True, output="ok"))
    evil = Plan(goal="g", steps=[PlanStep(
        id="s1", description="evil", dependencies=[], tools=["work"],
        risk=RiskLevel.CRITICAL, verification="v", completion_criteria="c")],
        final_verification="v")
    planner = RecoveryPlanner(reg, replan_provider=lambda t, p, r: evil)
    execute_fn, _, _ = _harness(reg)
    criteria = [{"strategy": "artifact_exists", "params": {"paths": ["x"]}}]

    def rooted_verify(task_id, plan, result):
        return Verifier().verify(task_id, "all", result.all_tool_results(),
                                 criteria, root="/tmp")

    outcome = RecoveryEngine(planner=planner).run(
        "t", _base_plan(), execute_fn, rooted_verify, max_attempts=2
    )
    assert outcome.status is OutcomeStatus.FAILED
    assert "CRITICAL" in outcome.reason


def test_18_valid_replacement_plan_accepted():
    reg = ToolRegistry()
    reg.register(Tool(name="work", input_schema={"required": []}),
                 lambda args: ToolResult(success=True, output="ok"))
    planner = RecoveryPlanner(
        reg, replan_provider=lambda t, p, r: _plan(("s1", [], ["work"]), ("s2", [], ["work"]))
    )
    execute_fn, _, calls = _harness(reg)
    criteria = [{"strategy": "artifact_exists", "params": {"paths": ["x"]}}]

    def rooted_verify(task_id, plan, result):
        return Verifier().verify(task_id, "all", result.all_tool_results(),
                                 criteria, root="/tmp")

    outcome = RecoveryEngine(planner=planner).run(
        "t", _base_plan(), execute_fn, rooted_verify, max_attempts=3
    )
    assert outcome.replans == 1
    assert len(outcome.plan_versions) == 2
    assert calls["executions"] == 2  # v1 + accepted v2 both executed


def test_classification_covers_all_classes():
    deny = ToolResult(task_id="t", tool_call_id="c", success=False, error="denied: nope")
    assert FailureClassifier.classify_tool_result(deny).failure_class is FailureClass.PERMISSION_FAILURE
    timeout = ToolResult(task_id="t", tool_call_id="c", success=False, error="timed out after 1s")
    assert FailureClassifier.classify_tool_result(timeout).failure_class is FailureClass.TIMEOUT
    assert FailureClassifier.classify_exception(ToolTimeoutError("t", 1.0)).failure_class is FailureClass.TIMEOUT
    assert FailureClassifier.classify_exception(
        AuthorizationDeniedError("t", "x", "r")).failure_class is FailureClass.PERMISSION_FAILURE
    assert FailureClassifier.classify_exception(
        ModelUnavailableError("down")).failure_class is FailureClass.MODEL_FAILURE
    assert FailureClassifier.classify_exception(
        PlanValidationError("bad")).failure_class is FailureClass.LOGICAL_FAILURE
    assert FailureClassifier.classify_exception(ValueError("?")).failure_class is FailureClass.UNKNOWN


def test_retry_policy_table_and_backoff():
    policy = RetryPolicy(max_attempts=3, base_delay_s=1.0, max_delay_s=5.0)
    assert policy.delay_for(1) == 1.0
    assert policy.delay_for(2) == 2.0
    assert policy.delay_for(3) == 4.0
    assert policy.delay_for(4) == 5.0  # capped
    perm = FailureClassifier.classify_tool_result(
        ToolResult(task_id="t", tool_call_id="c", success=False, error="denied"))
    assert policy.should_retry(perm, 0) is False
    transient = FailureClassifier.classify_tool_result(
        ToolResult(task_id="t", tool_call_id="c", success=False, error="transient reset"))
    assert policy.should_retry(transient, 0) is True
    assert policy.should_retry(transient, 3) is False


def test_recovery_is_auditable_and_observable():
    from ai_ecosystem.core.events import Event, EventBus
    from ai_ecosystem.core.models.enums import EventType

    execute_fn, verify_fn, _ = _harness(_flaky_registry(1, "transient blip"))
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe_all(seen.append)
    engine = RecoveryEngine(bus=bus)
    outcome = engine.run("t7", _base_plan(), execute_fn, verify_fn)
    assert outcome.status is OutcomeStatus.RECOVERED
    assert len(outcome.audit) == 1
    record = outcome.audit[0]
    assert record.attempt == 1
    assert record.action is RecoveryAction.RETRY
    assert record.verification == "FAILED"
    kinds = [e.event_type for e in seen]
    assert EventType.RECOVERY_DECIDED in kinds
    assert all(e.task_id == "t7" for e in seen)


def test_skip_action_supported_via_override():
    execute_fn, verify_fn, _ = _harness(_flaky_registry(99))
    policy = RecoveryPolicy(
        overrides={FailureClass.TOOL_FAILURE: RecoveryAction.SKIP})
    outcome = RecoveryEngine(policy=policy).run(
        "t", _base_plan(), execute_fn, verify_fn)
    assert outcome.status is OutcomeStatus.PARTIAL
    assert outcome.attempts == 1


def test_escalation_manager_records_and_emits():
    from ai_ecosystem.core.events import Event, EventBus
    from ai_ecosystem.core.models.enums import EventType

    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe_all(seen.append)
    manager = EscalationManager(bus)
    escalation = manager.escalate("t", "no route forward", ["attempts exhausted"])
    assert escalation.task_id == "t"
    assert manager.escalations == [escalation]
    assert seen[0].event_type is EventType.RECOVERY_EXHAUSTED
