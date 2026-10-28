"""Tool registry: named tools + handlers behind the standard contract.

A tool NEVER executes on registration -- handlers run only through
:class:`ToolRunner`, which enforces validation, risk, permission, and
observation first (Gate 6 wires the real authorizer; tests use stubs).
"""

from __future__ import annotations

from collections.abc import Callable
import threading

from ai_ecosystem.core.errors.exceptions import DomainValidationError
from ai_ecosystem.core.models.domain import Tool, ToolCall, ToolResult

ToolHandler = Callable[[dict], ToolResult]


class ToolRegistry:
    """Maps tool names to (contract, handler) pairs."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._handlers: dict[str, ToolHandler] = {}
        self._argument_guards: dict[str, Callable[[dict], str | None]] = {}
        self._lock = threading.RLock()

    def register(self, tool: Tool, handler: ToolHandler, argument_guard: Callable[[dict], str | None] | None = None) -> Tool:
        """Register a tool; duplicate names are rejected."""
        if not tool.name:
            raise DomainValidationError("tool name must not be empty")
        if not callable(handler):
            raise DomainValidationError(f"handler for {tool.name!r} is not callable")
        with self._lock:
            if tool.name in self._tools:
                raise DomainValidationError(f"tool {tool.name!r} already registered")
            self._tools[tool.name] = tool
            self._handlers[tool.name] = handler
            if argument_guard is not None:
                self._argument_guards[tool.name] = argument_guard
        return tool

    def argument_guard(self, name: str) -> Callable[[dict], str | None] | None:
        """Trusted, nonserializable pre-audit deny guard; never grants access."""
        with self._lock:
            return self._argument_guards.get(name)

    def unregister(self, name: str) -> None:
        """Revoke a dynamic tool without leaving a callable handler behind."""
        with self._lock:
            self._tools.pop(name, None)
            self._handlers.pop(name, None)
            self._argument_guards.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Contract for ``name`` (None when unknown)."""
        with self._lock:
            return self._tools.get(name)

    def handler(self, name: str) -> ToolHandler | None:
        """Handler for ``name`` (None when unknown)."""
        with self._lock:
            return self._handlers.get(name)

    def has(self, name: str) -> bool:
        """True when ``name`` is registered."""
        with self._lock:
            return name in self._tools

    def list_tools(self) -> list[Tool]:
        """All registered contracts, sorted by name."""
        with self._lock:
            return [self._tools[name] for name in sorted(self._tools)]

    def build_call(self, task_id: str, tool: str, arguments: dict) -> ToolCall:
        """Create a REQUESTED call record for a registered tool."""
        if tool not in self._tools:
            raise DomainValidationError(f"unknown tool {tool!r}")
        if not isinstance(arguments, dict):
            raise DomainValidationError("tool arguments must be a mapping")
        return ToolCall(task_id=task_id, tool=tool, arguments=dict(arguments))
