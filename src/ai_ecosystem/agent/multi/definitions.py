"""Multi-agent definitions and task ownership (Gate 18).

Agents are first-class runtime objects with identity, scope, and their
own tool allow-list. An AgentTask pins every unit of work to exactly
one owner (plus optional parentage), so no agent can act with another
agent's authority.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from ai_ecosystem.core.models.base import Entity
from ai_ecosystem.core.models.domain import Plan
from ai_ecosystem.core.models.enums import MemoryScope, RiskLevel


class AgentStatus(str, Enum):
    """Lifecycle of an agent or an agent-owned task."""

    CREATED = "CREATED"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AgentDefinition(Entity):
    """A specialized agent: capabilities, tools, scope, risk ceiling."""

    name: str = ""
    role: str = "general"
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    model_preferences: list[str] = Field(default_factory=list)
    scope: MemoryScope = MemoryScope.GLOBAL
    scope_id: str = ""
    risk_profile: RiskLevel = RiskLevel.MEDIUM
    status: AgentStatus = AgentStatus.CREATED


class AgentTask(Entity):
    """One unit of work owned by exactly one agent."""

    task_id: str = ""
    owner_agent: str = ""
    parent_agent: str = ""
    parent_task: str = ""
    goal: str = ""
    plan: Plan = Field(default_factory=Plan)
    arguments: dict[str, dict] = Field(default_factory=dict)
    status: AgentStatus = AgentStatus.CREATED
    interrupted: bool = False
    result_summary: str = ""
