"""ResearchManager: collect -> extract -> rank -> conflicts (Gate 11).

Coordinates the pipeline and emits lifecycle events. Source verification
(``verify_research``) reuses the Gate 9 result shape without pretending
to be autonomous fact-checking.
"""

from __future__ import annotations

from datetime import datetime

from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.base import utcnow
from ai_ecosystem.core.models.domain import VerificationResult
from ai_ecosystem.core.models.enums import EventType, VerificationStatus
from ai_ecosystem.intelligence.research.collector import Collected, SourceCollector
from ai_ecosystem.intelligence.research.models import ResearchQuery, ResearchResult
from ai_ecosystem.intelligence.research.ranking import (
    EvidenceExtractor,
    detect_conflicts,
    rank_evidence,
    rank_sources,
)
from ai_ecosystem.tools.registry.registry import ToolRegistry
from ai_ecosystem.tools.registry.runner import ToolRunner


class ResearchManager:
    """Runs the full research pipeline for one query."""

    def __init__(
        self,
        runner: ToolRunner,
        registry: ToolRegistry,
        search_tool: str = "web.search",
        bus: EventBus | None = None,
        collector: SourceCollector | None = None,
    ) -> None:
        self._collector = collector or SourceCollector(runner, registry, search_tool)
        self._extractor = EvidenceExtractor()
        self._bus = bus

    def research(self, query: ResearchQuery) -> ResearchResult:
        """Collect, extract, rank, and report -- conflicts preserved."""
        self._emit(EventType.RESEARCH_STARTED, "", {"query": query.query})
        collected = self._collector.collect(query)
        for reason in collected.rejected:
            self._emit(EventType.SOURCE_REJECTED, "", {"reason": reason})
        sources = collected.sources[: max(0, query.max_sources)]
        for source in sources:
            self._emit(
                EventType.SOURCE_COLLECTED,
                "",
                {"source_id": source.id, "origin": source.origin},
            )
        evidence = []
        for source in sources:
            items = self._extractor.extract(source, query.query)
            evidence.extend(items)
            self._emit(
                EventType.EVIDENCE_EXTRACTED,
                "",
                {"source_id": source.id, "evidence": len(items)},
            )
        evidence = [
            item for item in evidence if item.confidence >= query.min_confidence
        ]
        lookup = {source.id: source for source in sources}
        ranked_evidence = rank_evidence(evidence, lookup)
        ranked_sources = rank_sources(sources, ranked_evidence)
        conflicts = detect_conflicts(ranked_evidence)
        confidence = (
            round(
                sum(item.confidence for item in ranked_evidence) / len(ranked_evidence),
                3,
            )
            if ranked_evidence
            else 0.0
        )
        notes = [f"{len(collected.duplicates)} duplicate(s) merged."]
        if collected.error and not sources:
            notes.append(f"collection failed: {collected.error}")
            result = self._result(query, [], [], [], collected, confidence, notes)
            self._emit(EventType.RESEARCH_FAILED, "", {"error": collected.error})
            return result
        if collected.error and sources:
            notes.append(f"partial results after collection error: {collected.error}")
        if not sources:
            notes.append("no results for query")
        result = self._result(
            query,
            ranked_sources,
            ranked_evidence,
            conflicts,
            collected,
            confidence,
            notes,
        )
        self._emit(
            EventType.RESEARCH_COMPLETED,
            "",
            {
                "sources": len(sources),
                "evidence": len(ranked_evidence),
                "conflicts": len(conflicts),
            },
        )
        return result

    @staticmethod
    def _result(
        query: ResearchQuery,
        sources: list,
        evidence: list,
        conflicts: list,
        collected: Collected,
        confidence: float,
        notes: list[str],
    ) -> ResearchResult:
        return ResearchResult(
            query=query.query,
            sources=sources,
            evidence=evidence,
            conflicts=conflicts,
            duplicates=collected.duplicates,
            confidence=confidence,
            notes=notes,
        )

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(event_type=event_type, task_id=task_id, payload=payload)
            )


def verify_research(
    result: ResearchResult, max_age_days: float = 30.0, now: datetime | None = None
) -> VerificationResult:
    """Check source existence, metadata validity, freshness, and support.

    Uses the Gate 9 result shape. This is provenance hygiene, not
    fact-checking: it cannot tell whether a claim is true.
    """
    moment = now or utcnow()
    checks: list[str] = []
    evidence: list[str] = []
    failures: list[str] = []

    if not result.sources:
        failures.append("no sources collected")
    else:
        checks.append(f"{len(result.sources)} source(s) collected")
    for source in result.sources:
        problems = []
        if not source.origin:
            problems.append("missing origin")
        if not source.url and not source.content and not source.claims:
            problems.append("no retrievable content")
        age_days = (moment - source.retrieved_at).total_seconds() / 86400.0
        if age_days > max_age_days:
            problems.append(f"stale ({age_days:.1f} days old)")
        if problems:
            failures.append(f"{source.id[:8]}: {', '.join(problems)}")
        else:
            evidence.append(f"{source.id[:8]}: {source.origin} ok")
    if len(result.sources) == 1:
        checks.append("single source: claims are unsupported by corroboration")
    elif len(result.sources) > 1:
        checks.append(f"multi-source support: {len(result.sources)} sources")

    if failures:
        status, reason = VerificationStatus.FAILED, "; ".join(failures)
    else:
        status, reason = VerificationStatus.PASSED, "sources exist with valid metadata"
    return VerificationResult(
        task_id="",
        step_id="research",
        status=status,
        strategy="source_verification",
        checks=checks,
        evidence=evidence,
        reason=reason,
        metadata={"sources": len(result.sources), "evidence": len(result.evidence)},
    )
