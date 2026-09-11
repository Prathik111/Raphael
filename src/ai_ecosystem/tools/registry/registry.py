"""Tool registry: named tools + handlers behind the standard contract.

A tool NEVER executes on registration -- handlers run only through
:class:`ToolRunner`, which enforces validation, risk, permission, and
observation first (Gate 6 wires the real authorizer; tests use stubs).
"""

from __future__ import annotations

from collections.abc import Callable

from ai_ecosystem.core.errors.exceptions import DomainValidationError
from ai_ecosystem.core.models.domain import Tool, ToolCall, ToolResult

ToolHandler = Callable[[dict], ToolResult]


class ToolRegistry:
    """Maps tool names to (contract, handler) pairs."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, tool: Tool, handler: ToolHandler) -> Tool:
        """Register a tool; duplicate names are rejected."""
        if not tool.name:
            raise DomainValidationError("tool name must not be empty")
        if tool.name in self._tools:
            raise DomainValidationError(f"tool {tool.name!r} already registered")
        if not callable(handler):
            raise DomainValidationError(f"handler for {tool.name!r} is not callable")
        self._tools[tool.name] = tool
        self._handlers[tool.name] = handler
        return tool

    def get(self, name: str) -> Tool | None:
        """Contract for ``name`` (None when unknown)."""
        return self._tools.get(name)

    def handler(self, name: str) -> ToolHandler | None:
        """Handler for ``name`` (None when unknown)."""
        return self._handlers.get(name)

    def has(self, name: str) -> bool:
        """True when ``name`` is registered."""
        return name in self._tools

    def list_tools(self) -> list[Tool]:
        """All registered contracts, sorted by name."""
        return [self._tools[name] for name in sorted(self._tools)]

    def build_call(self, task_id: str, tool: str, arguments: dict) -> ToolCall:
        """Create a REQUESTED call record for a registered tool."""
        if tool not in self._tools:
            raise DomainValidationError(f"unknown tool {tool!r}")
        if not isinstance(arguments, dict):
            raise DomainValidationError("tool arguments must be a mapping")
        return ToolCall(task_id=task_id, tool=tool, arguments=dict(arguments))
