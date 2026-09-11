"""Awareness as a tool: one read-only snapshot tool (Gate 15).

The model never runs OS commands for awareness; it calls
``system.snapshot`` and receives a redacted summary (no process list,
no hostname). Full snapshots stay inside the manager/persistence path.
"""

from __future__ import annotations

from ai_ecosystem.core.models.domain import Tool, ToolResult
from ai_ecosystem.core.models.enums import RiskLevel
from ai_ecosystem.system.monitor.manager import SystemAwarenessManager


def awareness_tools(manager: SystemAwarenessManager) -> list[tuple[Tool, object]]:
    """Build the (contract, handler) pair for system.snapshot."""

    def _snapshot(arguments: dict) -> ToolResult:
        snap = manager.snapshot()
        return ToolResult(
            success=True,
            output={
                "os": snap.operating_system,
                "arch": snap.architecture,
                "pressure": snap.pressure.value,
                "capabilities": {
                    n: v for n, v in snap.capabilities.model_dump().items() if v
                },
                "cpu_logical": snap.cpu.logical_processors,
                "memory_total_bytes": snap.memory.total_bytes,
                "volumes": len(snap.storage),
                "gpus": len(snap.gpus),
            },
        )

    tool = Tool(
        name="system.snapshot",
        description="Take a redacted one-shot system snapshot.",
        input_schema={"required": []},
        risk_level=RiskLevel.LOW,
        capabilities=["read-only"],
    )
    return [(tool, _snapshot)]
