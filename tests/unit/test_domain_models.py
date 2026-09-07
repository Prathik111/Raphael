"""Gate 1: every core domain model instantiates with stable unique IDs."""

from ai_ecosystem.core import models as m


def _all_model_classes():
    return [
        m.Agent,
        m.Artifact,
        m.ComputeNode,
        m.Device,
        m.ExecutionContext,
        m.Goal,
        m.Memory,
        m.Model,
        m.ModelProvider,
        m.Permission,
        m.Plan,
        m.PlanStep,
        m.RiskAssessment,
        m.Skill,
        m.Task,
        m.Tool,
        m.ToolCall,
        m.ToolResult,
        m.VerificationResult,
    ]


def test_every_core_model_instantiates():
    for cls in _all_model_classes():
        obj = cls()
        assert obj.id, cls.__name__


def test_ids_are_stable_and_unique():
    a, b = m.Task(), m.Task()
    assert a.id != b.id
    assert a.created_at is not None and a.updated_at is not None


def test_task_defaults_to_created():
    assert m.Task().state == m.TaskState.CREATED


def test_plan_step_defaults():
    step = m.PlanStep(description="read docs")
    assert step.dependencies == []
    assert step.risk == m.RiskLevel.LOW


def test_tool_result_records_outcome():
    result = m.ToolResult(task_id="t", tool_call_id="c", success=True, output="ok")
    assert result.success is True
    assert result.error is None


def test_touch_refreshes_updated_at():
    task = m.Task()
    old = task.updated_at
    task.touch()
    assert task.updated_at >= old


def test_nested_plan_round_trip():
    plan = m.Plan(
        goal="report",
        steps=[m.PlanStep(description="read", completion_criteria="docs read")],
        final_verification="report exists",
    )
    restored = m.Plan.model_validate_json(plan.model_dump_json())
    assert restored.steps[0].description == "read"
    assert restored.final_verification == "report exists"
