"""Public research API."""

from ai_ecosystem.intelligence.research.collector import (
    Collected,
    SourceCollector,
    normalize_url,
    source_key,
)
from ai_ecosystem.intelligence.research.context import ContextBuilder
from ai_ecosystem.intelligence.research.manager import ResearchManager, verify_research
from ai_ecosystem.intelligence.research.models import (
    Conflict,
    DuplicateRef,
    Evidence,
    ResearchQuery,
    ResearchResult,
    Source,
)
from ai_ecosystem.intelligence.research.ranking import (
    EvidenceExtractor,
    detect_conflicts,
    evidence_score,
    freshness,
    rank_evidence,
    rank_sources,
    relevance,
    topic_of,
)

__all__ = [
    "Collected",
    "Conflict",
    "ContextBuilder",
    "DuplicateRef",
    "Evidence",
    "EvidenceExtractor",
    "ResearchManager",
    "ResearchQuery",
    "ResearchResult",
    "Source",
    "SourceCollector",
    "detect_conflicts",
    "evidence_score",
    "freshness",
    "normalize_url",
    "rank_evidence",
    "rank_sources",
    "relevance",
    "source_key",
    "topic_of",
    "verify_research",
]
