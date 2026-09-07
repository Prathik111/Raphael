"""Gate 1: ExecutionContext serialization / restoration."""

import pytest

from ai_ecosystem.core.errors import ContextSerializationError
from ai_ecosystem.core.models import (
    Artifact,
    ExecutionContext,
    Permission,
    Plan,
    PlanStep,
    RiskAssessment,
    TaskState,
    ToolResult,
)


def _rich_context() -> ExecutionContext:
    return ExecutionContext(
        task_id="task-1",
        goal="inspect repository",
        current_state=TaskState.EXECUTING,
        plan=Plan(
            goal="inspect repository",
            steps=[
                PlanStep(description="read", completion_criteria="done"),
                PlanStep(description="report", completion_criteria="done"),
            ],
            final_verification="report exists",
        ),
        current_step="read",
        variables={"repo": "AiEcosystem"},
        tool_results=[ToolResult(task_id="task-1", tool_call_id="c1", success=True)],
        artifacts=[Artifact(task_id="task-1", name="report.md", uri="file://r")],
        observations=["saw structure"],
        permissions=[Permission(task_id="task-1")],
        risk_assessments=[RiskAssessment(task_id="task-1")],
        errors=[],
        metadata={"attempt": 1},
    )


def test_snapshot_restore_round_trip():
    ctx = _rich_context()
    restored = ExecutionContext.restore(ctx.snapshot())
    assert restored.task_id == "task-1"
    assert restored.current_state == TaskState.EXECUTING
    assert restored.plan is not None and len(restored.plan.steps) == 2
    assert restored.variables == {"repo": "AiEcosystem"}
    assert len(restored.tool_results) == 1
    assert restored.artifacts[0].name == "report.md"
    assert restored.metadata == {"attempt": 1}


def test_restore_preserves_identity():
    ctx = _rich_context()
    assert ExecutionContext.restore(ctx.snapshot()).id == ctx.id


def test_restore_garbage_raises():
    with pytest.raises(ContextSerializationError):
        ExecutionContext.restore("{not valid json")


def test_empty_context_round_trip():
    ctx = ExecutionContext(task_id="t")
    restored = ExecutionContext.restore(ctx.snapshot())
    assert restored.plan is None
    assert restored.tool_results == []
