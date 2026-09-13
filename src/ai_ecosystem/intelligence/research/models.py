"""Structured research models (Gate 11).

Provenance is structural: every Evidence names its Source, every
Conflict names its Evidence, and duplicates are recorded rather than
silently dropped. Nothing here executes anything.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ai_ecosystem.core.models.base import Entity, utcnow

STOPWORDS = frozenset(
    "a an the and or but of to in on for with is are was were be been "
    "it its this that these those as at by from into over after such no "
    "not only also than then there their what when where which who will "
    "can has have had do does did how why vs via per".split()
)


class ResearchQuery(Entity):
    """What to research and how much to gather."""

    query: str = ""
    max_sources: int = Field(default=5, ge=0, le=100)
    max_age_days: float = 30.0
    min_confidence: float = 0.0


class Source(Entity):
    """One retrieved source; content is UNTRUSTED DATA (never executed)."""

    title: str = ""
    origin: str = ""
    url: str = ""
    retrieved_at: datetime = Field(default_factory=utcnow)
    content: str = ""
    claims: list[str] = Field(default_factory=list)
    quality: float = 0.5
    metadata: dict = Field(default_factory=dict)


class Evidence(Entity):
    """One claim with its provenance and deterministic scores."""

    source_id: str = ""
    claim: str = ""
    reference: str = ""
    topic: str = ""
    confidence: float = 0.0
    relevance: float = 0.0


class Conflict(Entity):
    """Differing claims on the same topic, provenance preserved."""

    topic: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class DuplicateRef(BaseModel):
    """A retrieval merged into an earlier identical source."""

    source_id: str = ""
    duplicate_of: str = ""
    reason: str = ""


class ResearchResult(Entity):
    """Everything a research run produced, conflicts included."""

    query: str = ""
    sources: list[Source] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    duplicates: list[DuplicateRef] = Field(default_factory=list)
    confidence: float = 0.0
    notes: list[str] = Field(default_factory=list)
