"""FastMCP bridge (Gate 5: MCP must not bypass the policy layer).

Adapted tools are registered in the :class:`ToolRegistry` like any other
tool, so they flow through validation -> risk -> permission -> execution.
The ``fastmcp`` package itself is optional: adaptation works on any
duck-typed tool object, and only live server connections require it.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Callable

from ai_ecosystem.core.errors.exceptions import ToolExecutionError
from ai_ecosystem.core.models.domain import Tool, ToolResult
from ai_ecosystem.core.models.enums import RiskLevel


def is_available() -> bool:
    """True when the ``fastmcp`` package can be imported."""
    try:
        import fastmcp  # noqa: F401
    except ImportError:
        return False
    return True


def adapt_tool(
    name: str,
    description: str,
    input_schema: dict,
    fn: Callable[[dict], Any],
    risk_level: RiskLevel = RiskLevel.MEDIUM,
) -> tuple[Tool, Callable[[dict], ToolResult]]:
    """Wrap a plain function as a (contract, handler) pair.

    The function receives the arguments mapping and returns a JSON-able
    value; exceptions become :class:`ToolExecutionError`.
    """

    def _handler(arguments: dict) -> ToolResult:
        try:
            return ToolResult(success=True, output=fn(dict(arguments)))
        except ToolExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001 -- isolate foreign tools
            raise ToolExecutionError(name, str(exc)) from exc

    tool = Tool(
        name=name,
        description=description or "",
        input_schema=dict(input_schema or {}),
        risk_level=risk_level,
        capabilities=["mcp-adapted"],
    )
    return tool, _handler


def connect_server(url: str) -> Any:
    """Connect to a live FastMCP server (requires the package)."""
    if not is_available():
        raise ToolExecutionError(
            "mcp",
            "fastmcp is not installed; cannot connect to "
            f"{url!r}. Install fastmcp to enable live MCP servers.",
        )
    import fastmcp  # type: ignore[import-not-found]

    return fastmcp.Client(url)
