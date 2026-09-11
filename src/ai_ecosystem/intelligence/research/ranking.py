"""Deterministic evidence extraction, ranking, and conflict detection."""

from __future__ import annotations

import re
from datetime import datetime

from ai_ecosystem.core.models.base import utcnow
from ai_ecosystem.intelligence.research.models import (
    STOPWORDS,
    Conflict,
    Evidence,
    Source,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def significant_words(text: str) -> list[str]:
    """Lowercased content words in order of appearance."""
    return [
        word
        for word in re.findall(r"[a-z0-9]+", text.lower())
        if word not in STOPWORDS and len(word) > 2
    ]


def relevance(query: str, claim: str) -> float:
    """Keyword overlap of claim against query (0..1), deterministic."""
    query_words = set(significant_words(query))
    if not query_words:
        return 0.0
    return len(query_words & set(significant_words(claim))) / len(query_words)


def topic_of(claim: str) -> str:
    """Coarse topic: first three significant words (or 'general')."""
    words = significant_words(claim)[:3]
    return " ".join(words) if words else "general"


def split_sentences(content: str) -> list[str]:
    """Naive sentence split; keeps chunks with real content."""
    return [
        part.strip()
        for part in _SENTENCE_SPLIT.split(content)
        if len(part.strip()) >= 20
    ]


class EvidenceExtractor:
    """Turns source content into evidenced claims (text only, always)."""

    def extract(self, source: Source, query: str) -> list[Evidence]:
        """Extract claims; explicit source.claims win over sentence split."""
        claims = [c.strip() for c in source.claims if c.strip()]
        if not claims:
            claims = split_sentences(source.content)
        findings = []
        for claim in claims:
            rel = round(relevance(query, claim), 3)
            findings.append(
                Evidence(
                    source_id=source.id,
                    claim=claim,
                    reference=claim[:160],
                    topic=topic_of(claim),
                    relevance=rel,
                    confidence=round(min(0.95, 0.4 + 0.6 * rel), 3),
                )
            )
        return findings


def freshness(retrieved_at: datetime, now: datetime) -> float:
    """1.0 when fresh, decaying over ~30 days (ranking signal only)."""
    age_days = max(0.0, (now - retrieved_at).total_seconds() / 86400.0)
    return round(1.0 / (1.0 + age_days / 30.0), 3)


def evidence_score(evidence: Evidence, source: Source, now: datetime) -> float:
    """Ranking signal (NOT truth): relevance, freshness, source quality."""
    return round(
        0.5 * evidence.relevance
        + 0.3 * freshness(source.retrieved_at, now)
        + 0.2 * min(1.0, max(0.0, source.quality)),
        3,
    )


def rank_evidence(
    evidence: list[Evidence], sources: dict[str, Source], now: datetime | None = None
) -> list[Evidence]:
    """Deterministic best-first ordering (stable for ties)."""
    moment = now or utcnow()
    scored = [
        (evidence_score(item, sources[item.source_id], moment), index, item)
        for index, item in enumerate(evidence)
        if item.source_id in sources
    ]
    scored.sort(key=lambda triple: (-triple[0], triple[1]))
    return [item for _, _, item in scored]


def rank_sources(
    sources: list[Source], evidence: list[Evidence], now: datetime | None = None
) -> list[Source]:
    """Sources ordered by their mean evidence score (0 when none)."""
    moment = now or utcnow()
    by_source: dict[str, list[float]] = {s.id: [] for s in sources}
    lookup = {s.id: s for s in sources}
    for item in evidence:
        if item.source_id in lookup:
            by_source[item.source_id].append(
                evidence_score(item, lookup[item.source_id], moment)
            )

    def mean(source_id: str) -> float:
        scores = by_source[source_id]
        return sum(scores) / len(scores) if scores else 0.0

    return sorted(sources, key=lambda s: -mean(s.id))


def detect_conflicts(evidence: list[Evidence]) -> list[Conflict]:
    """Group by topic; differing claims from different sources conflict.

    Nothing is merged or resolved -- the Conflict preserves every side.
    """
    by_topic: dict[str, list[Evidence]] = {}
    for item in evidence:
        by_topic.setdefault(item.topic, []).append(item)
    conflicts = []
    for topic, items in sorted(by_topic.items()):
        variants: dict[str, Evidence] = {}
        for item in items:
            variants.setdefault(item.claim.strip().lower(), item)
        if len(variants) < 2:
            continue
        sources = sorted({item.source_id for item in variants.values()})
        if len(sources) < 2:
            continue
        ordered = [variants[key] for key in sorted(variants)]
        conflicts.append(
            Conflict(
                topic=topic,
                evidence_ids=[item.id for item in ordered],
                claims=[item.claim for item in ordered],
                source_ids=sources,
                reason=(
                    f"{len(ordered)} differing claims on one topic "
                    f"from {len(sources)} sources; preserved, not merged"
                ),
            )
        )
    return conflicts
