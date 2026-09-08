"""Core domain models (BUILD_PLAN Gate 1).

Contracts only: field declarations plus serialization helpers. No
reasoning, planning, execution, or LLM logic may live here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from ai_ecosystem.core.errors.exceptions import ContextSerializationError
from ai_ecosystem.core.models.base import Entity
from ai_ecosystem.core.models.enums import (
    MemoryScope,
    MemoryStatus,
    MemoryType,
    NodeType,
    PermissionDecision,
    RiskLevel,
    SkillStatus,
    SkillType,
    TaskState,
    ToolCallStatus,
    VerificationStatus,
)


class Goal(Entity):
    """A user goal the agent must accomplish."""

    title: str = ""
    description: str = ""
    success_criteria: list[str] = Field(default_factory=list)


class Task(Entity):
    """A unit of work tracked through the PACE state machine."""

    title: str = ""
    goal_id: Optional[str] = None
    state: TaskState = TaskState.CREATED
    agent_id: Optional[str] = None


class PlanStep(Entity):
    """One structured step of a plan (Gate 7 produces these).

    ``arguments`` carries model-proposed tool arguments for the step.
    It is data, not trust: calls still pass registry-contract
    validation, risk assessment, and policy authorization, and
    operator-pinned ``AgentConfig.arguments`` override it per key.
    """

    description: str = ""
    dependencies: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    risk: RiskLevel = RiskLevel.LOW
    verification: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    completion_criteria: str = ""


class Plan(Entity):
    """A structured plan: goal + steps + final verification."""

    goal: str = ""
    steps: list[PlanStep] = Field(default_factory=list)
    final_verification: str = ""


class Tool(Entity):
    """Tool contract (Gate 5 lifecycle; Gate 30 sandbox flags)."""

    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.LOW
    capabilities: list[str] = Field(default_factory=list)
    isolation: str = "none"
    timeout_seconds: Optional[float] = None


class ToolResult(Entity):
    """Observed result of a tool call."""

    task_id: str = ""
    tool_call_id: str = ""
    success: bool = False
    output: Any = None
    error: Optional[str] = None
    exit_code: Optional[int] = None


class Permission(Entity):
    """An authorization decision for a (task, tool-call) pair."""

    task_id: str = ""
    tool_call_id: Optional[str] = None
    decision: PermissionDecision = PermissionDecision.PENDING
    reason: str = ""
    policy: str = "default"


class RiskAssessment(Entity):
    """Risk evaluation for a proposed action (Gate 6 engine later)."""

    task_id: str = ""
    tool_call_id: Optional[str] = None
    level: RiskLevel = RiskLevel.LOW
    factors: list[str] = Field(default_factory=list)
    rationale: str = ""


class Artifact(Entity):
    """A file or object produced during execution."""

    task_id: str = ""
    name: str = ""
    kind: str = "file"
    uri: str = ""
    sha256: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Memory(Entity):
    """One structured memory record (Gate 12 pipeline manages these).

    DATA only: memory never influences authorization or policy. Scope is
    a str-enum so pre-Gate-12 snapshots (scope ``"global"``) still load.

    Trust metadata is intentionally separate from confidence/importance.
    A memory can be highly relevant yet still be untrusted model/data
    input. Callers must never treat these fields as authorization.
    """

    type: MemoryType = MemoryType.SEMANTIC
    content: str = ""
    source: str = ""
    confidence: float = 1.0
    importance: float = 0.5
    scope: MemoryScope = MemoryScope.GLOBAL
    scope_id: str = ""
    status: MemoryStatus = MemoryStatus.ACTIVE
    retention_days: Optional[int] = None
    cloud_eligible: bool = False
    provenance: str = "unknown"
    created_by: str = "unknown"
    verified: bool = False
    expires_at: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Skill(Entity):
    """A structured, versioned, permission-aware capability (Gate 17).

    A skill packages a validated workflow (plan template + verification
    criteria). It grants nothing: invocation builds a normal Plan that
    passes validation, risk, policy, and ToolRunner like any other.
    Versions are immutable records; updates create new versions.
    """

    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    code_ref: str = ""
    skill_type: SkillType = SkillType.USER_DEFINED
    status: SkillStatus = SkillStatus.ACTIVE
    allowed_tools: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    risk_class: RiskLevel = RiskLevel.LOW
    workflow: list[dict[str, Any]] = Field(default_factory=list)
    verification: list[dict[str, Any]] = Field(default_factory=list)
