"""Part 8 end-to-end: full EXECUTE -> VERIFY -> RECOVER -> VERIFY cycles.

Both tests wire the real stack (ParallelExecutor + ToolRunner +
Verifier + RecoveryEngine) with stub tools and a mock-free verifier.
"""

from ai_ecosystem.agent import ParallelExecutor
from ai_ecosystem.agent.recovery import OutcomeStatus, RecoveryEngine, RecoveryPlanner
from ai_ecosystem.agent.verifier import Verifier
from ai_ecosystem.core.models import Plan, PlanStep, Tool, ToolResult
from ai_ecosystem.tools import GrantAllAuthorizer, ToolRegistry, ToolRunner


def _plan(*items):
    steps = [
        PlanStep(id=sid, description=sid, dependencies=list(deps), tools=list(tools),
                 verification="v", completion_criteria="c")
        for sid, deps, tools in items
    ]
    return Plan(goal="g", steps=steps, final_verification="v")


def _stack(registry):
    runner = ToolRunner(registry, GrantAllAuthorizer())
    return ParallelExecutor(runner, registry), Verifier()


def test_e2e_fail_classify_recover_verify_success():
    """EXECUTE -> FAIL -> CLASSIFY -> RETRY -> EXECUTE -> VERIFY -> SUCCESS."""
    state = {"n": 0}

    def flaky(args):
        from ai_ecosystem.core.errors import ToolExecutionError

        state["n"] += 1
        if state["n"] == 1:
            raise ToolExecutionError("work", "transient network reset")
        return ToolResult(success=True, output="ok")

    registry = ToolRegistry()
    registry.register(Tool(name="work", input_schema={"required": []}), flaky)
    executor, verifier = _stack(registry)
    criteria = [{"strategy": "command_results"}]

    outcome = RecoveryEngine().run(
        "t",
        _plan(("s1", [], ["work"])),
        lambda plan, args: executor.execute("t", plan),
        lambda task_id, plan, result: verifier.verify(
            task_id, "all", result.all_tool_results(), criteria),
    )
    assert outcome.status is OutcomeStatus.RECOVERED
    assert outcome.attempts == 2
    assert outcome.final_verification is not None
    assert outcome.final_verification.status.value == "PASSED"
    assert [r.action.value for r in outcome.audit] == ["RETRY"]


def test_e2e_false_success_verify_fail_recover_final_failure():
    """EXECUTE -> FALSE SUCCESS -> VERIFY FAIL -> REPLAN -> VERIFY FAIL -> FINAL."""
    registry = ToolRegistry()
    registry.register(Tool(name="work", input_schema={"required": []}),
                      lambda args: ToolResult(success=True, output="claimed ok"))
    executor, _ = _stack(registry)
    criteria = [{"strategy": "artifact_exists", "params": {"paths": ["proof.txt"]}}]

    def verify_fn(task_id, plan, result):
        return Verifier().verify(task_id, "all", result.all_tool_results(),
                                 criteria, root="/tmp/definitely-absent-dir")

    planner = RecoveryPlanner(
        registry,
        replan_provider=lambda t, p, r: _plan(("s1", [], ["work"]), ("s2", [], ["work"])),
    )
    outcome = RecoveryEngine(planner=planner).run(
        "t",
        _plan(("s1", [], ["work"])),
        lambda plan, args: executor.execute("t", plan),
        verify_fn,
        max_attempts=3,
    )
    # False success caught by verification, replan attempted, still failing.
    assert outcome.status in (OutcomeStatus.ESCALATED, OutcomeStatus.FAILED)
    assert outcome.replans == 1
    assert outcome.final_verification is not None
    assert outcome.final_verification.status.value == "FAILED"
    actions = [r.action.value for r in outcome.audit]
    assert "REPLAN" in actions
    # The audit tells the whole story: false success does not escape.
    assert any(r.classification.value == "VERIFICATION_FAILURE" for r in outcome.audit)
