"""Provenance-preserving context builder (Gate 11).

Renders a ResearchResult as model-facing text WITHOUT losing source
boundaries: every claim stays inside its source section with origin,
confidence, and evidence id. Never an opaque blob.
"""

from __future__ import annotations

from ai_ecosystem.intelligence.research.models import ResearchResult


class ContextBuilder:
    """Builds readable, fully-attributed research context."""

    def build(self, result: ResearchResult) -> str:
        """Render the result as text with explicit source sections."""
        lines = [
            f"Research: {result.query}",
            f"Confidence: {result.confidence:.2f} "
            f"({len(result.evidence)} evidence from {len(result.sources)} sources)",
            "",
        ]
        by_source: dict[str, list] = {}
        for item in result.evidence:
            by_source.setdefault(item.source_id, []).append(item)
        for index, source in enumerate(result.sources, 1):
            lines.append(f"--- Source {index}: {source.title or source.origin} ---")
            lines.append(f"Origin: {source.origin}")
            if source.url:
                lines.append(f"URL: {source.url}")
            lines.append(f"Quality: {source.quality:.2f}")
            items = by_source.get(source.id, [])
            if not items:
                lines.append("(no evidence extracted)")
            for item in items:
                lines.append(
                    f"- [{item.id[:8]}|conf {item.confidence:.2f}] {item.claim}"
                )
            lines.append("")
        if result.conflicts:
            lines.append("--- Conflicts (unresolved, all sides preserved) ---")
            for conflict in result.conflicts:
                lines.append(f"Topic: {conflict.topic}")
                for claim, source_id in zip(conflict.claims, conflict.source_ids):
                    lines.append(f"  x [{source_id[:8]}] {claim}")
            lines.append("")
        if result.duplicates:
            lines.append(f"Note: {len(result.duplicates)} duplicate source(s) merged.")
        if result.notes:
            lines.append("Notes:")
            lines.extend(f"- {note}" for note in result.notes)
        return "\n".join(lines).strip()
