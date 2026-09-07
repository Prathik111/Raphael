"""Public multi-agent API (shared runtime; no second executor)."""

from ai_ecosystem.agent.multi.coordinator import Coordination, Coordinator
from ai_ecosystem.agent.multi.definitions import (
    AgentDefinition,
    AgentStatus,
    AgentTask,
)
from ai_ecosystem.agent.multi.manager import (
    AgentManager,
    AgentRegistry,
    AgentTaskRepository,
    SubtaskSpec,
    Supervisor,
    SupervisorResult,
)
from ai_ecosystem.agent.multi.messages import (
    AgentMessage,
    MessageBus,
    MessageType,
    expire_in,
)

__all__ = [
    "AgentDefinition",
    "AgentManager",
    "AgentMessage",
    "AgentRegistry",
    "AgentStatus",
    "AgentTask",
    "AgentTaskRepository",
    "Coordination",
    "Coordinator",
    "MessageBus",
    "MessageType",
    "SubtaskSpec",
    "Supervisor",
    "SupervisorResult",
    "expire_in",
]
