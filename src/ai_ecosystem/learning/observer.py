"""Usage observer, aggregation, patterns, and proposals (Gate 16).

Pipeline: events -> observer (policy-gated) -> aggregation ->
patterns -> proposals -> explicit adoption. Security policy is not
reachable from any of these paths: proposal kinds exclude it, and
adoption targets only memory/preference stores passed in by the caller.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ai_ecosystem.core.errors.exceptions import DomainValidationError
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.enums import EventType, MemoryScope, MemoryType
from ai_ecosystem.learning.models import (
    LearningProposal,
    ObservationMode,
    ObservationPolicy,
    ProposalKind,
    ProposalStatus,
    UsageEvent,
    UsagePattern,
)

_FORBIDDEN_KINDS = (
    "policy",
    "policies",
    "permission",
    "permissions",
    "authorize",
    "authorized",
    "authorization",
    "risk",
    "sandbox",
    "secret",
    "secrets",
)

# In-memory session bound: long-lived processes must not grow forever.
_SESSION_CAP = 10_000


def _has_forbidden_word(text: str, hints: tuple[str, ...]) -> bool:
    """Whole-token match so "keyboard"/"tokenizer" don't false-positive."""
    import re

    tokens = set(re.split(r"[^a-z0-9]+", text.lower()))
    return any(hint in tokens for hint in hints)


class UsageObserver:
    """Policy-gated event sink: DISABLED records nothing at all."""

    def __init__(
        self,
        policy: ObservationPolicy | None = None,
        repository: Any | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._policy = policy or ObservationPolicy()
        self._repository = repository
        self._bus = bus
        self._session: list[UsageEvent] = []

    @property
    def mode(self) -> ObservationMode:
        """Current observation mode."""
        return self._policy.mode

    def set_mode(self, mode: ObservationMode) -> None:
        """Operator control for observation."""
        self._policy.mode = mode

    def observe_tool(
        self,
        tool: str,
        success: bool,
        duration_ms: float = 0.0,
        project: str = "",
        agent: str = "",
    ) -> UsageEvent | None:
        """Record a tool use (name only -- never arguments or outputs)."""
        return self.record(
            UsageEvent(
                category="tool",
                action=tool,
                project_scope=project,
                agent_scope=agent,
                duration_ms=duration_ms,
                success=success,
            )
        )

    def observe_model(
        self, model: str, success: bool, task_kind: str = "", project: str = ""
    ) -> UsageEvent | None:
        """Record a model use (name only -- never prompts)."""
        return self.record(
            UsageEvent(
                category="model",
                action=model,
                project_scope=project,
                duration_ms=0.0,
                success=success,
                metadata={"task_kind": task_kind} if task_kind else {},
            )
        )

    def observe_task(
        self,
        outcome: str,
        success: bool,
        duration_ms: float = 0.0,
        project: str = "",
        agent: str = "",
    ) -> UsageEvent | None:
        """Record a task outcome (no goal text, no results)."""
        return self.record(
            UsageEvent(
                category="task",
                action=outcome,
                project_scope=project,
                agent_scope=agent,
                duration_ms=duration_ms,
                success=success,
            )
        )

    def record(self, event: UsageEvent) -> UsageEvent | None:
        """Store per policy; returns None when observation is disabled.

        Session memory is capped (oldest dropped first); persistence
        failures roll back the session append so the two never diverge.
        """
        mode = self._policy.mode
        if mode is ObservationMode.DISABLED:
            return None
        if mode in (
            ObservationMode.LOCAL_PERSISTENCE,
            ObservationMode.LEARNING_ENABLED,
        ):
            if self._repository is None:
                raise DomainValidationError("persistence mode requires a repository")
            stored = self._repository.create(event)
            self._session.append(stored)
        else:
            self._session.append(event)
        del self._session[:-_SESSION_CAP]
        return event

    def session_events(self) -> list[UsageEvent]:
        """In-memory events of this process (all modes except DISABLED)."""
        return list(self._session)

    def stored_events(self) -> list[UsageEvent]:
        """Persisted events (empty unless a persistence mode is active)."""
        if self._repository is None:
            return []
        return self._repository.list()


class UsageAggregator:
    """Deterministic counts over normalized events."""

    def __init__(self, events: list[UsageEvent]) -> None:
        self._events = list(events)

    def counts(self) -> dict[tuple[str, str], int]:
        """(category, action) -> occurrences."""
        return dict(Counter((e.category, e.action) for e in self._events))

    def success_rate(self, category: str, action: str) -> float | None:
        """Fraction successful (None when never observed)."""
        relevant = [
            e for e in self._events if e.category == category and e.action == action
        ]
        if not relevant:
            return None
        return sum(1 for e in relevant if e.success) / len(relevant)

    def average_duration_ms(self, category: str, action: str) -> float | None:
        """Mean duration (None when never observed)."""
        relevant = [
            e for e in self._events if e.category == category and e.action == action
        ]
        if not relevant:
            return None
        return sum(e.duration_ms for e in relevant) / len(relevant)

    def sequences(self, category: str, length: int = 2) -> dict[tuple[str, ...], int]:
        """Ordered action n-grams within one category (workflow shapes)."""
        actions = [e.action for e in self._events if e.category == category]
        grams: Counter[tuple[str, ...]] = Counter()
        for index in range(len(actions) - length + 1):
            grams[tuple(actions[index : index + length])] += 1
        return dict(grams)


class PatternDetector:
    """Threshold-based pattern mining over aggregations."""

    def __init__(self, min_evidence: int = 3, smoothing: float = 5.0) -> None:
        self._min_evidence = min_evidence
        self._smoothing = smoothing

    def _confidence(self, evidence: int) -> float:
        return round(evidence / (evidence + self._smoothing), 3)

    def detect(self, events: list[UsageEvent], scope: str = "") -> list[UsagePattern]:
        """Mine tool/model/workflow/failure patterns deterministically."""
        aggregator = UsageAggregator(events)
        patterns: list[UsagePattern] = []
        for (category, action), count in sorted(aggregator.counts().items()):
            if category == "tool" and count >= self._min_evidence:
                patterns.append(
                    UsagePattern(
                        pattern_type="frequently_used_tool",
                        description=f"Tool {action!r} used {count} times.",
                        evidence_count=count,
                        confidence=self._confidence(count),
                        scope=scope,
                        evidence=[f"{category}:{action}x{count}"],
                    )
                )
            if category == "model" and count >= self._min_evidence:
                patterns.append(
                    UsagePattern(
                        pattern_type="frequently_used_model",
                        description=f"Model {action!r} used {count} times.",
                        evidence_count=count,
                        confidence=self._confidence(count),
                        scope=scope,
                        evidence=[f"{category}:{action}x{count}"],
                    )
                )
            rate = aggregator.success_rate(category, action)
            if rate is not None and rate < 0.5 and count >= self._min_evidence:
                patterns.append(
                    UsagePattern(
                        pattern_type="repeated_failure",
                        description=f"{category}:{action} fails often ({rate:.0%} success).",
                        evidence_count=count,
                        confidence=self._confidence(count),
                        scope=scope,
                        evidence=[f"{category}:{action}x{count}"],
                    )
                )
        for sequence, count in sorted(aggregator.sequences("tool").items()):
            if count >= self._min_evidence:
                patterns.append(
                    UsagePattern(
                        pattern_type="repeated_workflow",
                        description=f"Workflow {' -> '.join(sequence)} seen {count} times.",
                        evidence_count=count,
                        confidence=self._confidence(count),
                        scope=scope,
                        evidence=[" -> ".join(sequence)] * count,
                    )
                )
        return patterns


class ProposalEngine:
    """Turns patterns into reviewable proposals (never auto-applies)."""

    _KIND_FOR_PATTERN = {
        "frequently_used_tool": ProposalKind.WORKFLOW_HINT,
        "frequently_used_model": ProposalKind.MODEL_HINT,
        "repeated_workflow": ProposalKind.WORKFLOW_HINT,
        "repeated_failure": ProposalKind.WORKFLOW_HINT,
    }

    def __init__(self, bus: EventBus | None = None) -> None:
        self._bus = bus

    def propose(self, pattern: UsagePattern) -> LearningProposal:
        """Build a proposal; refuses anything resembling security content."""
        kind = self._KIND_FOR_PATTERN.get(
            pattern.pattern_type, ProposalKind.WORKFLOW_HINT
        )
        content = self._content_for(pattern, kind)
        if _has_forbidden_word(content, _FORBIDDEN_KINDS):
            raise DomainValidationError("proposals must never touch security policy")
        proposal = LearningProposal(
            kind=kind,
            content=content,
            pattern_id=pattern.id,
            confidence=pattern.confidence,
            scope=pattern.scope,
            provenance={
                "pattern_type": pattern.pattern_type,
                "evidence": list(pattern.evidence),
                "evidence_count": pattern.evidence_count,
            },
        )
        self._emit(
            EventType.LEARNING_PROPOSAL_CREATED,
            "",
            {"proposal_id": proposal.id, "kind": kind.value},
        )
        return proposal

    @staticmethod
    def _content_for(pattern: UsagePattern, kind: ProposalKind) -> str:
        if kind is ProposalKind.MODEL_HINT:
            return f"Consider preferring {pattern.description}"
        return f"Noticed pattern: {pattern.description}"

    def adopt_to_memory(self, proposal: LearningProposal, memories: Any) -> Any:
        """Adopt as a Gate 12 memory candidate (explicit operator action)."""
        from ai_ecosystem.personalization.memory.models import MemoryCandidate

        self._require_proposed(proposal)
        candidate = MemoryCandidate(
            content=proposal.content,
            type=MemoryType.EXPERIENCE,
            source=f"learning:{proposal.pattern_id}",
            confidence=proposal.confidence,
            importance=proposal.confidence,
            scope=MemoryScope.GLOBAL,
            reason="adopted learning proposal",
            metadata={"proposal_id": proposal.id},
        )
        created = memories.store(candidate)
        proposal.status = ProposalStatus.ADOPTED
        self._emit(
            EventType.PROPOSAL_ADOPTED,
            "",
            {"proposal_id": proposal.id, "memory_id": created.id},
        )
        return created

    def adopt_to_preferences(
        self, proposal: LearningProposal, preferences: Any, project_id: str = ""
    ) -> Any:
        """Adopt workflow/model hints as preferences (explicit action)."""
        from ai_ecosystem.personalization.personality.profiles import PreferenceProfile

        self._require_proposed(proposal)
        if proposal.kind not in (
            ProposalKind.WORKFLOW_HINT,
            ProposalKind.MODEL_HINT,
            ProposalKind.PREFERENCE,
        ):
            raise DomainValidationError(
                f"kind {proposal.kind.value} cannot become a preference"
            )
        profile = PreferenceProfile(
            scope=MemoryScope.PROJECT if project_id else MemoryScope.GLOBAL,
            scope_id=project_id,
            preferred_workflows=[proposal.content],
        )
        created = preferences.save(profile)
        proposal.status = ProposalStatus.ADOPTED
        proposal.provenance["adopted_scope"] = created.scope.value
        proposal.provenance["adopted_scope_id"] = created.scope_id
        self._emit(
            EventType.PROPOSAL_ADOPTED,
            "",
            {"proposal_id": proposal.id, "preference_id": created.id},
        )
        return created

    def reject(self, proposal: LearningProposal, reason: str = "") -> LearningProposal:
        """Decline a proposal (recorded, auditable)."""
        self._require_proposed(proposal)
        proposal.status = ProposalStatus.REJECTED
        self._emit(
            EventType.PROPOSAL_REJECTED,
            "",
            {"proposal_id": proposal.id, "reason": reason},
        )
        return proposal

    @staticmethod
    def _require_proposed(proposal: LearningProposal) -> None:
        if proposal.status is not ProposalStatus.PROPOSED:
            raise DomainValidationError(f"proposal already {proposal.status.value}")

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(event_type=event_type, task_id=task_id, payload=payload)
            )
