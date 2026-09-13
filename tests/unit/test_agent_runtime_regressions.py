from pydantic import BaseModel

from ai_ecosystem.agent.planner.backend import ModelReasoningBackend
from ai_ecosystem.intelligence.models.providers import (
    MockModelProvider,
    ModelResponse,
    request_structured,
)


class TaskSpec(BaseModel):
    title: str = ""
    constraints: list[str] = []
    desired_outcome: str = ""
    needs_research: bool = False


def test_structured_understanding_falls_back_to_goal_for_non_json_reply() -> None:
    provider = MockModelProvider(handler=lambda _: ModelResponse(text="I can help with that."))

    result = request_structured(
        provider,
        __import__("ai_ecosystem.intelligence.models.providers", fromlist=["ModelRequest"]).ModelRequest(
            prompt="UNDERSTAND: say hello"
        ),
        TaskSpec,
    )

    assert result.desired_outcome == "say hello"
    assert result.needs_research is False


def test_conversational_goal_uses_direct_response_without_planner_json() -> None:
    provider = MockModelProvider(handler=lambda _: ModelResponse(text="Hello from Raphael."))
    planner = ModelReasoningBackend(provider)

    plan = planner.plan("hello", ["agent.respond"])

    assert plan.steps[0].tools == ["agent.respond"]
    assert plan.steps[0].arguments["text"] == "Hello from Raphael."
    assert len(provider.calls) == 1
