"""Memory lifecycle models (Gate 12).

Observations become MemoryCandidates; candidates are evaluated before
anything persists. Consolidation is always propose-then-apply: history
is never silently rewritten.
"""

from __future__ import annotations

from pydantic import Field

from ai_ecosystem.core.models.base import Entity
from ai_ecosystem.core.models.enums import MemoryScope, MemoryType


class MemoryCandidate(Entity):
    """A proposed memory awaiting importance evaluation."""

    content: str = ""
    type: MemoryType = MemoryType.SEMANTIC
    source: str = ""
    confidence: float = 0.5
    importance: float = 0.5
    scope: MemoryScope = MemoryScope.GLOBAL
    scope_id: str = ""
    reason: str = ""
    metadata: dict = Field(default_factory=dict)


class ConsolidationProposal(Entity):
    """A validated plan to merge memories: one new record, olds archived."""

    new_candidate: MemoryCandidate = Field(default_factory=MemoryCandidate)
    archive_ids: list[str] = Field(default_factory=list)
    reason: str = ""
