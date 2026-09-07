"""Usage observation models (Gate 16).

UsageEvents record *that* something happened (tool name, success,
duration) -- never raw sensitive content such as file bodies, prompts,
or arguments. Patterns aggregate events; proposals are explicit,
reviewable suggestions. Nothing here changes behavior by itself.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from ai_ecosystem.core.models.base import Entity, utcnow


class ObservationMode(str, Enum):
    """How much history the user allows (explicit control)."""

    DISABLED = "DISABLED"
    SESSION_ONLY = "SESSION_ONLY"
    LOCAL_PERSISTENCE = "LOCAL_PERSISTENCE"
    LEARNING_ENABLED = "LEARNING_ENABLED"


class UsageEvent(Entity):
    """One normalized usage fact (sanitized at creation)."""

    category: str = ""
    action: str = ""
    project_scope: str = ""
    agent_scope: str = ""
    duration_ms: float = 0.0
    success: bool = True
    resource_summary: str = ""
    metadata: dict = Field(default_factory=dict)


class UsagePattern(Entity):
    """A detected regularity with evidence and confidence."""

    pattern_type: str = ""
    description: str = ""
    evidence_count: int = 0
    confidence: float = 0.0
    scope: str = ""
    evidence: list[str] = Field(default_factory=list)


class ProposalKind(str, Enum):
    """What a proposal may suggest (security kinds do not exist)."""

    PREFERENCE = "PREFERENCE"
    WORKFLOW_HINT = "WORKFLOW_HINT"
    MODEL_HINT = "MODEL_HINT"
    COMMUNICATION = "COMMUNICATION"


class ProposalStatus(str, Enum):
    """Lifecycle of a learning proposal."""

    PROPOSED = "PROPOSED"
    ADOPTED = "ADOPTED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


class LearningProposal(Entity):
    """A reviewable suggestion derived from a pattern (never auto-applied)."""

    kind: ProposalKind = ProposalKind.PREFERENCE
    content: str = ""
    pattern_id: str = ""
    confidence: float = 0.0
    scope: str = ""
    status: ProposalStatus = ProposalStatus.PROPOSED
    provenance: dict = Field(default_factory=dict)


class ObservationPolicy(BaseModel):
    """Operator control for usage observation."""

    mode: ObservationMode = ObservationMode.DISABLED
    updated_at: datetime = Field(default_factory=utcnow)
