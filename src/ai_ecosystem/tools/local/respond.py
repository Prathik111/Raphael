"""agent.respond: answer directly when no other tool is needed.

Greetings, explanations, and questions answerable from the goal itself
need no filesystem, shell, or network access. The model puts its answer
in the ``text`` argument; the handler returns it unchanged as the tool
output, and the orchestrator captures respond outputs as the task's
reply. LOW risk: it touches nothing and exfiltrates nothing (the text
was model-generated to begin with).
"""

from __future__ import annotations

from ai_ecosystem.core.errors.exceptions import ToolExecutionError
from ai_ecosystem.core.models.domain import Tool, ToolResult
from ai_ecosystem.core.models.enums import RiskLevel

MAX_REPLY_CHARS = 8_000


def _respond(arguments: dict) -> ToolResult:
    text = arguments.get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise ToolExecutionError("agent.respond", "missing 'text' argument")
    if len(text) > MAX_REPLY_CHARS:
        raise ToolExecutionError(
            "agent.respond",
            f"reply too long ({len(text)} chars, cap {MAX_REPLY_CHARS})",
        )
    return ToolResult(success=True, output=text)


def respond_tools() -> list[tuple[Tool, object]]:
    """Build the (contract, handler) pair for agent.respond."""
    return [
        (
            Tool(
                name="agent.respond",
                description="Reply to the user directly with free text. "
                "Use this (and only this) when the goal needs "
                "no other tool: greetings, explanations, or "
                "answers you already know. Put the full reply "
                "in the 'text' argument.",
                input_schema={"required": ["text"], "properties": {"text": "string"}},
                risk_level=RiskLevel.LOW,
                capabilities=["respond"],
            ),
            _respond,
        ),
    ]
