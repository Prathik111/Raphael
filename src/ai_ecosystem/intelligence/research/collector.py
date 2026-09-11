"""Source collection through the existing tool path (Gate 11).

The ONLY outbound call is a registered search tool invoked via
ToolRunner, so authorization, risk, events, and timeouts apply exactly
as for any other tool. Source *content* is never interpreted as
instructions -- it is validated into Source records or rejected.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from ai_ecosystem.agent.recovery.classification import FailureClassifier
from ai_ecosystem.agent.recovery.policy import RetryPolicy
from ai_ecosystem.intelligence.research.models import (
    DuplicateRef,
    ResearchQuery,
    Source,
)
from ai_ecosystem.tools.registry.registry import ToolRegistry
from ai_ecosystem.tools.registry.runner import ToolRunner


def normalize_url(url: str) -> str:
    """Canonical form for dedup: lowercase, no fragment, no trailing slash."""
    cleaned = url.strip().lower().split("#", 1)[0].rstrip("/")
    return cleaned


def source_key(source: Source) -> str:
    """Dedup identity: normalized URL, else a content hash."""
    normalized = normalize_url(source.url)
    if normalized:
        return f"url:{normalized}"
    digest = hashlib.sha256(source.content.encode("utf-8")).hexdigest()
    return f"content:{digest}"


@dataclass
class Collected:
    """Raw outcome of one collection run."""

    sources: list[Source] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    duplicates: list[DuplicateRef] = field(default_factory=list)
    attempts: int = 0
    error: str = ""


class SourceCollector:
    """Gathers sources via a search tool, with bounded retry and dedup."""

    def __init__(
        self,
        runner: ToolRunner,
        registry: ToolRegistry,
        search_tool: str = "web.search",
        retry_policy: RetryPolicy | None = None,
        max_attempts: int = 2,
    ) -> None:
        self._runner = runner
        self._registry = registry
        self._search_tool = search_tool
        self._retry_policy = retry_policy or RetryPolicy(max_attempts=1)
        self._max_attempts = max(1, max_attempts)

    def collect(self, query: ResearchQuery, task_id: str = "") -> Collected:
        """Run the search tool; validate, dedup, and return sources."""
        last_error = ""
        attempts = 0
        for attempt in range(1, self._max_attempts + 1):
            attempts = attempt
            call = self._registry.build_call(
                task_id,
                self._search_tool,
                {"query": query.query, "max_results": query.max_sources},
            )
            try:
                result = self._runner.run(call)
            except Exception as exc:  # noqa: BLE001 -- runner never raises, defensive
                last_error = f"runner raised: {exc}"
                break
            if result.success:
                return self._parse(result.output, attempts)
            last_error = result.error or "search tool failed"
            classification = FailureClassifier.classify_tool_result(result)
            if not self._retry_policy.should_retry(classification, attempt - 1):
                break
        return Collected(attempts=attempts, error=last_error)

    def _parse(self, output: Any, attempts: int) -> Collected:
        collected = Collected(attempts=attempts)
        entries = output.get("sources", []) if isinstance(output, dict) else []
        seen: dict[str, str] = {}
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                collected.rejected.append(f"entry {index}: not a mapping")
                continue
            try:
                source = Source.model_validate(entry)
            except Exception as exc:
                collected.rejected.append(f"entry {index}: invalid ({exc})")
                continue
            if not source.origin:
                collected.rejected.append(f"entry {index}: missing origin")
                continue
            if not source.content and not source.claims:
                collected.rejected.append(f"entry {index}: empty content")
                continue
            key = source_key(source)
            if key in seen:
                collected.duplicates.append(
                    DuplicateRef(
                        source_id=source.id,
                        duplicate_of=seen[key],
                        reason="identical source reference already collected",
                    )
                )
                continue
            seen[key] = source.id
            collected.sources.append(source)
        return collected
