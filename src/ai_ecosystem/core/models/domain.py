"""Core domain models (BUILD_PLAN Gate 1).

Contracts only: field declarations plus serialization helpers. No
reasoning, planning, execution, or LLM logic may live here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

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
    goal_id: str | None = None
    state: TaskState = TaskState.CREATED
    agent_id: str | None = None


class PlanStep(Entity):
    """One structured step of a plan (Gate 7 produces these)."""

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
    timeout_s: float = 60.0
    capabilities: list[str] = Field(default_factory=list)
    execution_policy: str = "default"
    requires_sandbox: bool = False
    sandbox_profile: str = "default"


class ToolCall(Entity):
    """A request to execute a tool within a task."""

    task_id: str = ""
    tool: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: ToolCallStatus = ToolCallStatus.REQUESTED


class ToolResult(Entity):
    """Observed outcome of a tool call."""

    task_id: str = ""
    tool_call_id: str = ""
    success: bool = False
    output: Any = None
    error: str | None = None
    exit_code: int | None = None


class Permission(Entity):
    """An authorization decision for a (task, tool-call) pair."""

    task_id: str = ""
    tool_call_id: str | None = None
    decision: PermissionDecision = PermissionDecision.PENDING
    reason: str = ""
    policy: str = "default"


class RiskAssessment(Entity):
    """Risk evaluation for a proposed action (Gate 6 engine later)."""

    task_id: str = ""
    tool_call_id: str | None = None
    level: RiskLevel = RiskLevel.LOW
    factors: list[str] = Field(default_factory=list)
    rationale: str = ""


class Artifact(Entity):
    """A file or object produced during execution."""

    task_id: str = ""
    name: str = ""
    kind: str = "file"
    uri: str = ""
    sha256: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Memory(Entity):
    """One structured memory record.

    Memory is untrusted DATA. Provenance and verification describe where
    a memory came from; neither field grants authority or bypasses policy.
    """

    type: MemoryType = MemoryType.SEMANTIC
    content: str = ""
    source: str = ""
    confidence: float = 1.0
    importance: float = 0.5
    scope: MemoryScope = MemoryScope.GLOBAL
    scope_id: str = ""
    status: MemoryStatus = MemoryStatus.ACTIVE
    retention_days: int | None = None
    cloud_eligible: bool = False
    provenance: str = "unknown"
    created_by: str = "unknown"
    verified: bool = False
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Skill(Entity):
    """A structured, versioned, permission-aware capability (Gate 17)."""

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
    author: str = ""
    scope: MemoryScope = MemoryScope.GLOBAL
    scope_id: str = ""


class ModelProvider(Entity):
    """An interchangeable model backend (Gate 4 wires these up)."""

    name: str = ""
    kind: str = "local"


class Model(Entity):
    """A selectable reasoning model behind the abstraction."""

    name: str = ""
    provider_id: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    context_length: int = 0


class Device(Entity):
    """A known ecosystem node (Gate 35 standardizes the protocol)."""

    name: str = ""
    node_type: NodeType = NodeType.PC
    capabilities: list[str] = Field(default_factory=list)
    online: bool = False


class ComputeNode(Entity):
    """A schedulable compute target (Gate 25 routes to these)."""

    device_id: str | None = None
    kind: str = "local"
    available: bool = True
    resources: dict[str, Any] = Field(default_factory=dict)


class VerificationResult(Entity):
    """Structured verification outcome."""

    task_id: str = ""
    step_id: str = ""
    status: VerificationStatus = VerificationStatus.PENDING
    level: int = 1
    strategy: str = ""
    checks: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    reason: str = ""
    message: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class Agent(Entity):
    """An agent identity (orchestrator or subagent)."""

    name: str = ""
    role: str = "general"
    capabilities: list[str] = Field(default_factory=list)
    model_id: str | None = None
    permission_scope: str = "default"


class ExecutionContext(Entity):
    """All durable state for one running task."""

    task_id: str = ""
    goal: str = ""
    current_state: TaskState = TaskState.CREATED
    plan: Plan | None = None
    current_step: str | None = None
    variables: dict[str, Any] = Field(default_factory=dict)
    tool_results: list[ToolResult] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    permissions: list[Permission] = Field(default_factory=list)
    risk_assessments: list[RiskAssessment] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def snapshot(self) -> str:
        try:
            return self.model_dump_json()
        except Exception as exc:
            raise ContextSerializationError(
                f"cannot serialize context {self.id}: {exc}"
            ) from exc

    @classmethod
    def restore(cls, data: str) -> ExecutionContext:
        try:
            return cls.model_validate_json(data)
        except Exception as exc:
            raise ContextSerializationError(f"cannot restore context: {exc}") from exc
