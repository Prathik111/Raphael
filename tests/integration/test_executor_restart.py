"""Gate 8 restart: A=SUCCEEDED, B=RUNNING, C=PENDING at shutdown.

After restart the graph must restore consistently: A stays SUCCEEDED, B
returns to PENDING flagged interrupted (its thread died with the
process -- the graph never pretends otherwise), C stays PENDING. Gate 10
will decide B's fate; here B simply re-runs to prove recoverability.
"""

from ai_ecosystem.agent import ParallelExecutor, TaskGraph
from ai_ecosystem.core.models import Goal, Plan, PlanStep, Tool, ToolResult
from ai_ecosystem.core.models.enums import StepState
from ai_ecosystem.core.runtime import AgentRuntime
from ai_ecosystem.tools import GrantAllAuthorizer, ToolRegistry, ToolRunner


def _plan():
    steps = [
        PlanStep(id="a", description="a", dependencies=[], tools=["ok"],
                 verification="v", completion_criteria="c"),
        PlanStep(id="b", description="b", dependencies=["a"], tools=["ok"],
                 verification="v", completion_criteria="c"),
        PlanStep(id="c", description="c", dependencies=["b"], tools=["ok"],
                 verification="v", completion_criteria="c"),
    ]
    return Plan(goal="g", steps=steps, final_verification="v")


def _registry():
    reg = ToolRegistry()
    reg.register(
        Tool(name="ok", input_schema={"required": []}),
        lambda args: ToolResult(success=True, output="ok"),
    )
    return reg


def test_restart_restores_consistent_graph(tmp_path):
    db_path = str(tmp_path / "eco.db")
    plan = _plan()

    # --- first process: run A, leave B RUNNING, C PENDING, then die ---
    first = AgentRuntime(db_path)
    try:
        task, ctx = first.submit_goal(Goal(title="restartable"))
        ctx.plan = plan
        graph = TaskGraph.from_plan(plan)
        graph.get("a").transition(StepState.READY)
        graph.get("a").transition(StepState.RUNNING)
        graph.get("a").transition(StepState.SUCCEEDED)
        graph.get("b").transition(StepState.READY)
        graph.get("b").transition(StepState.RUNNING)
        ctx.metadata["graph"] = graph.snapshot()
        first.contexts_repo.save(ctx)
        task_id = task.id
    finally:
        first.shutdown()  # simulated kill: no graceful executor teardown

    # --- second process: restore and verify consistency ---
    second = AgentRuntime(db_path)
    try:
        resumed_ctx = second.manager.get_context(task_id)
        assert resumed_ctx.plan is not None
        restored = TaskGraph.restore(resumed_ctx.plan, resumed_ctx.metadata["graph"])
    finally:
        second.shutdown()

    assert restored.get("a").state is StepState.SUCCEEDED
    assert restored.get("b").state is StepState.PENDING
    assert restored.get("b").interrupted is True
    assert "restart" in restored.get("b").error
    assert restored.get("c").state is StepState.PENDING
    assert not restored.get("c").interrupted

    # --- re-execution from the restored graph completes honestly ---
    registry = _registry()
    runner = ToolRunner(registry, GrantAllAuthorizer())
    executor = ParallelExecutor(runner, registry)
    result = executor.execute_graph(task_id, restored)
    assert [s for s in result.succeeded] == ["a", "b", "c"]
