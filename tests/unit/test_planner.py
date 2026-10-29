"""Gate 7: structured plans only -- validation rejects everything unrunnable."""

import pytest

from ai_ecosystem.agent import (
    DependencyResolver,
    ModelReasoningBackend,
    PlanValidator,
)
from ai_ecosystem.core.errors import ModelMalformedError, PlanValidationError
from ai_ecosystem.core.models import Plan, PlanStep
from ai_ecosystem.core.models.enums import RiskLevel
from ai_ecosystem.intelligence import MockModelProvider, ModelResponse

TOOLS = {"filesystem.read", "filesystem.list"}

VALID = {
    "goal": "Create project report",
    "steps": [
        {
            "id": "s1",
            "description": "Read source documents",
            "dependencies": [],
            "tools": ["filesystem.read"],
            "risk": "LOW",
            "verification": "content captured",
            "completion_criteria": "sources read",
        },
        {
            "id": "s2",
            "description": "Generate report",
            "dependencies": ["s1"],
            "tools": ["filesystem.read"],
            "risk": "LOW",
            "verification": "report exists",
            "completion_criteria": "report written",
        },
    ],
    "final_verification": "report verified",
}


def _backend(payload):
    return ModelReasoningBackend(
        MockModelProvider("m", handler=lambda req: ModelResponse(structured=payload)),
        known_tools=set(TOOLS),
    )


def test_truncated_plan_for_explicit_search_uses_validated_query():
    backend = ModelReasoningBackend(MockModelProvider("m", handler=lambda req: ModelResponse(text='{"goal": "unfinished"')), max_repair_attempts=1)
    plan = backend.plan("search the web about claude ipo valuation", ["web.search"])
    assert plan.steps[0].tools == ["web.search"]
    assert plan.steps[0].arguments == {"query": "claude ipo valuation", "max_results": 5}


def test_truncated_plan_for_unrelated_action_still_fails():
    backend = ModelReasoningBackend(MockModelProvider("m", handler=lambda req: ModelResponse(text='{"goal": "unfinished"')), max_repair_attempts=1)
    with pytest.raises(ModelMalformedError):
        backend.plan("delete the archive", ["web.search"])


def test_date_question_uses_trusted_clock_without_model_plan():
    backend = ModelReasoningBackend(MockModelProvider("m", handler=lambda req: ModelResponse(text="not JSON")))
    plan = backend.plan("what date is it today", ["system.date", "agent.respond"])
    assert plan.steps[0].tools == ["system.date"]


@pytest.mark.parametrize("question", ["todays date", "today's date", "What is today's date?", "what date is it today", "Please tell me today's date.", "Can you tell me what day is it today?"])
def test_date_question_variants_share_clock_routing(question):
    from ai_ecosystem.tools.local.clock import is_date_question

    assert is_date_question(question)


@pytest.mark.parametrize("question", ["Search today's news", "Schedule a meeting on today's date", "What happened on this date in 1980?"])
def test_date_routing_preserves_other_tasks(question):
    from ai_ecosystem.tools.local.clock import is_date_question

    assert not is_date_question(question)


def _step(sid, deps=(), tools=("filesystem.read",), **kw):
    args = {
        "id": sid,
        "description": f"step {sid}",
        "dependencies": list(deps),
        "tools": list(tools),
        "verification": "checked",
        "completion_criteria": "done",
    }
    args.update(kw)
    return PlanStep(**args)


def test_valid_plan_accepted():
    plan = _backend(VALID).plan("Create project report", sorted(TOOLS))
    assert plan.goal == "Create project report"
    assert [s.id for s in plan.steps] == ["s1", "s2"]


def test_missing_dependency_rejected():
    payload = dict(VALID)
    payload["steps"] = [dict(VALID["steps"][1], dependencies=["ghost"])]
    with pytest.raises(PlanValidationError, match="unknown step"):
        _backend(payload).plan("g", sorted(TOOLS))


def test_circular_dependency_rejected():
    payload = dict(VALID)
    payload["steps"] = [
        dict(VALID["steps"][0], dependencies=["s2"]),
        dict(VALID["steps"][1], dependencies=["s1"]),
    ]
    with pytest.raises(PlanValidationError, match="cycle"):
        _backend(payload).plan("g", sorted(TOOLS))


def test_nonexistent_tool_rejected():
    payload = dict(VALID)
    payload["steps"] = [dict(VALID["steps"][0], tools=["teleport"])]
    with pytest.raises(PlanValidationError, match="unknown tools"):
        _backend(payload).plan("g", sorted(TOOLS))


def test_step_without_tools_rejected():
    validator = PlanValidator(TOOLS)
    with pytest.raises(PlanValidationError, match="names no tools"):
        validator.validate(Plan(goal="g", steps=[_step("s1", tools=[])], final_verification="v"))


def test_unsafe_critical_plan_rejected():
    validator = PlanValidator(TOOLS)
    step = _step("s1", risk=RiskLevel.CRITICAL)
    with pytest.raises(PlanValidationError, match="CRITICAL"):
        validator.validate(Plan(goal="g", steps=[step], final_verification="v"))


def test_incomplete_completion_criteria_rejected():
    validator = PlanValidator(TOOLS)
    step = _step("s1", completion_criteria="  ")
    with pytest.raises(PlanValidationError, match="completion criteria"):
        validator.validate(Plan(goal="g", steps=[step], final_verification="v"))


def test_malformed_model_output_rejected():
    backend = ModelReasoningBackend(
        MockModelProvider("m", handler=lambda req: ModelResponse(text="nope{{")),
    )
    with pytest.raises(ModelMalformedError):
        backend.plan("g", sorted(TOOLS))


def test_resolver_orders_diamond():
    steps = [
        _step("a"),
        _step("b", deps=("a",)),
        _step("c", deps=("a",)),
        _step("d", deps=("b", "c")),
    ]
    ordered = [s.id for s in DependencyResolver.order(steps)]
    assert ordered[0] == "a" and ordered[-1] == "d"
    assert set(ordered[1:3]) == {"b", "c"}


def test_resolver_rejects_self_dependency():
    with pytest.raises(PlanValidationError, match="itself"):
        DependencyResolver.order([_step("a", deps=("a",))])


def test_empty_plan_rejected():
    with pytest.raises(PlanValidationError, match="no steps"):
        PlanValidator(TOOLS).validate(Plan(goal="g", steps=[], final_verification="v"))


def test_missing_step_arguments_rejected_when_contracts_known():
    validator = PlanValidator({"terminal.execute"}, {"terminal.execute": {"command"}})
    step = _step("s1", tools=("terminal.execute",))
    with pytest.raises(PlanValidationError, match="missing required arguments"):
        validator.validate(Plan(goal="g", steps=[step], final_verification="v"))


def test_step_arguments_satisfy_contracts():
    from ai_ecosystem.core.models import PlanStep

    validator = PlanValidator({"terminal.execute"}, {"terminal.execute": {"command"}})
    step = PlanStep(
        id="s1",
        description="d",
        tools=["terminal.execute"],
        arguments={"command": ["echo", "hi"]},
        verification="v",
        completion_criteria="c",
    )
    plan = validator.validate(Plan(goal="g", steps=[step], final_verification="v"))
    assert plan.steps[0].arguments == {"command": ["echo", "hi"]}


def test_step_argument_wrong_type_rejected():
    from ai_ecosystem.core.models import PlanStep

    validator = PlanValidator(
        {"terminal.execute"},
        tool_schemas={
            "terminal.execute": {"required": ["command"], "properties": {"command": "array"}}
        },
    )
    step = PlanStep(
        id="s1",
        description="d",
        tools=["terminal.execute"],
        arguments={"command": "echo hi"},
        verification="v",
        completion_criteria="c",
    )
    with pytest.raises(PlanValidationError, match="must be array"):
        validator.validate(Plan(goal="g", steps=[step], final_verification="v"))


def test_backend_repairs_missing_arguments():
    calls = []
    missing = dict(VALID)
    missing["steps"] = [dict(VALID["steps"][0])]
    fixed = dict(VALID)
    fixed["steps"] = [
        dict(VALID["steps"][0], arguments={"path": "a.txt"}),
        dict(VALID["steps"][1], arguments={"path": "b.txt"}),
    ]

    def handle(req):
        calls.append(req.prompt)
        return ModelResponse(structured=missing if len(calls) == 1 else fixed)

    backend = ModelReasoningBackend(
        MockModelProvider("m", handler=handle),
        known_tools=set(TOOLS),
        tool_arguments={"filesystem.read": {"path"}},
    )
    plan = backend.plan("g", sorted(TOOLS))
    assert plan.steps[0].arguments == {"path": "a.txt"}
    assert len(calls) == 2
