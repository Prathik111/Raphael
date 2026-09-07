"""Public orchestrator API (single agent; multi-agent lands Gate 15)."""

from ai_ecosystem.agent.orchestrator.single_agent import (
    AgentConfig,
    AgentResult,
    SingleAgent,
    TaskSpec,
)

__all__ = ["AgentConfig", "AgentResult", "SingleAgent", "TaskSpec"]
