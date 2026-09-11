"""Controlled self-learning pipeline (Gate 32).

Observation -> Pattern -> Proposal -> Evidence -> Validation ->
Approval -> Adoption. Every arrow is explicit and reversible; nothing
here modifies behavior by itself. Adoption targets are memory,
preferences, and skill proposals only -- permissions, policy,
credentials, sandboxing, and authorization are unreachable by
construction (no imports, no parameters, no code paths).
"""

from __future__ import annotations

from typing import Any

from ai_ecosystem.core.errors.exceptions import DomainValidationError
from ai_ecosystem.core.events.bus import EventBus
from ai_ecosystem.learning.models import (
    LearningProposal,
    ProposalStatus,
    UsageEvent,
)
from ai_ecosystem.learning.observer import (
    PatternDetector,
    ProposalEngine,
)
from ai_ecosystem.learning.safety import LearningGovernor, LearningRisk


class LearningPipeline:
    """Generates, validates, and adopts learning proposals with gates."""

    def __init__(
        self,
        proposals: Any,
        engine: ProposalEngine | None = None,
        detector: PatternDetector | None = None,
        governor: LearningGovernor | None = None,
        min_confidence: float = 0.3,
        bus: EventBus | None = None,
    ) -> None:
        self._proposals = proposals
        self._engine = engine or ProposalEngine(bus)
        self._detector = detector or PatternDetector()
        self._governor = governor
        self._min_confidence = min_confidence
        self._bus = bus

    def generate(
        self, events: list[UsageEvent], scope: str = ""
    ) -> list[LearningProposal]:
        """Detect patterns and file validated proposals (persisted)."""
        created = []
        for pattern in self._detector.detect(events, scope):
            if pattern.confidence < self._min_confidence:
                continue
            proposal = self._engine.propose(pattern)
            created.append(self._proposals.create(proposal))
        return created

    def validate(self, proposal: LearningProposal) -> tuple[bool, str]:
        """Evidence + confidence + kind gate (no behavior change)."""
        if proposal.confidence < self._min_confidence:
            return False, f"confidence {proposal.confidence} below minimum"
        if not proposal.provenance.get("evidence"):
            return False, "proposal lacks evidence"
        if self._governor is not None:
            risk = self._governor.classify(proposal)
            if risk is LearningRisk.CRITICAL:
                return False, "critical learning content refused"
        return True, "proposal valid"

    def approve(
        self,
        proposal_id: str,
        target: str = "memory",
        stores: dict[str, Any] | None = None,
        project_id: str = "",
    ) -> Any:
        """Adopt a validated proposal (explicit operator action)."""
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise DomainValidationError(f"unknown proposal {proposal_id!r}")
        valid, reason = self.validate(proposal)
        if not valid:
            raise DomainValidationError(f"proposal invalid: {reason}")
        if self._governor is not None and not self._governor.may_adopt(proposal):
            raise DomainValidationError("adoption blocked by learning governor")
        stores = stores or {}
        if target == "memory":
            memories = stores.get("memories")
            if memories is None:
                raise DomainValidationError("no memory store provided")
            created = self._engine.adopt_to_memory(proposal, memories)
        elif target == "preference":
            preferences = stores.get("preferences")
            if preferences is None:
                raise DomainValidationError("no preference store provided")
            created = self._engine.adopt_to_preferences(
                proposal, preferences, project_id
            )
        else:
            raise DomainValidationError(f"unknown adoption target {target!r}")
        proposal.provenance["adopted_object"] = getattr(created, "id", "")
        proposal.provenance["adopted_target"] = target
        self._proposals.update(proposal)
        return created

    def reject(self, proposal_id: str, reason: str = "") -> LearningProposal:
        """Decline a proposal (persisted decision)."""
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise DomainValidationError(f"unknown proposal {proposal_id!r}")
        rejected = self._engine.reject(proposal, reason)
        return self._proposals.update(rejected)

    def rollback(
        self, proposal_id: str, stores: dict[str, Any] | None = None
    ) -> bool:
        """Reverse an adoption (memory delete / preference revert)."""
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise DomainValidationError(f"unknown proposal {proposal_id!r}")
        if proposal.status is not ProposalStatus.ADOPTED:
            raise DomainValidationError("only adopted proposals roll back")
        stores = stores or {}
        target = proposal.provenance.get("adopted_target", "")
        adopted_id = proposal.provenance.get("adopted_object", "")
        if target == "memory":
            memories = stores.get("memories")
            if memories is None:
                raise DomainValidationError("no memory store provided")
            memories.delete(adopted_id)
        elif target == "preference":
            preferences = stores.get("preferences")
            if preferences is None:
                raise DomainValidationError("no preference store provided")
            self._revert_preference(preferences, proposal)
        else:
            raise DomainValidationError(f"cannot roll back target {target!r}")
        proposal.status = ProposalStatus.ROLLED_BACK
        proposal.provenance["rolled_back"] = True
        self._proposals.update(proposal)
        return True

    def _revert_preference(self, preferences: Any, proposal: LearningProposal) -> None:
        from ai_ecosystem.personalization.personality.profiles import PreferenceProfile

        scope_id = proposal.provenance.get("adopted_scope_id", "")
        if scope_id:
            current = preferences.get_project(scope_id)
            if current is None:
                raise DomainValidationError("adopted profile is gone")
        else:
            current = preferences.get_global()
        revert = PreferenceProfile(
            scope=current.scope,
            scope_id=current.scope_id,
            preferred_workflows=[
                w
                for w in current.preferred_workflows
                if w != proposal.content and proposal.content not in w
            ],
            preferred_tools=list(current.preferred_tools),
            output_format=current.output_format,
            defaults={
                k: v
                for k, v in current.defaults.items()
                if proposal.content not in str(v)
            },
        )
        preferences.save(revert)

    def detect_conflicts(
        self, proposals: list[LearningProposal]
    ) -> list[tuple[str, str]]:
        """Pairs of same-scope proposals with overlapping keywords."""
        from ai_ecosystem.personalization.memory.store import keywords

        pairs = []
        for index, first in enumerate(proposals):
            for second in proposals[index + 1 :]:
                if first.scope != second.scope:
                    continue
                if keywords(first.content) & keywords(second.content):
                    pairs.append((first.id, second.id))
        return pairs
