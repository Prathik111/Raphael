"""Read the local system date without a model or network request."""

from __future__ import annotations

from datetime import datetime
import re

from ai_ecosystem.core.models.domain import Tool, ToolResult
from ai_ecosystem.core.models.enums import RiskLevel
from ai_ecosystem.tools.registry.registry import ToolHandler


def is_date_question(goal: str) -> bool:
    """Recognize standalone local-date requests, preserving compound tasks."""
    text = goal.lower().replace("’", "'")
    text = re.sub(r"\bwhat'?s\b", "what is", text)
    text = re.sub(r"\btoday'?s\b", "today", text)
    text = " ".join(re.sub(r"[?!.:,]", " ", text).split())
    text = re.sub(r"^(?:please )?(?:(?:can|could|would) you )?(?:tell me|give me|show me)\s+", "", text)
    text = re.sub(r"^please\s+", "", text)
    return bool(re.fullmatch(
        r"(?:what (?:is )?)?(?:the )?(?:(?:current|today) )?(?:date|day)(?: is it)?(?: today| right now)?"
        r"|what (?:date|day) is today|what is today|today date please",
        text,
    ))


def _date(_arguments: dict) -> ToolResult:
    current = datetime.now().astimezone()
    return ToolResult(success=True, output=f"Today is {current.strftime('%A, %B')} {current.day}, {current.year} ({current.strftime('%Z')}).")


def clock_tools() -> list[tuple[Tool, ToolHandler]]:
    return [(Tool(name="system.date", description="Read today's date and local time zone from the system clock.", input_schema={"required": []}, risk_level=RiskLevel.LOW, capabilities=["clock.read"]), _date)]
