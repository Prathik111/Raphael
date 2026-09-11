"""Proactive suggestions with operator controls (Gate 34).

Triggers propose; they never execute. A proposal needs explicit
approval unless it is LOW risk inside an auto-approving configuration,
and even then it runs through injected execute/verify callables --
never a private execution path. Anti-spam (dedupe, cooldown, quiet
hours, frequency caps) is structural, and proposals cannot create
triggers, so self-triggering cycles are impossible by construction.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any
from collections.abc import Callable

from ai_ecosystem.core.errors.exceptions import DomainValidationError
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.base import Entity
from ai_ecosystem.core.models.enums import EventType
from ai_ecosystem.learning.safety import LearningRisk


class TriggerKind(str, Enum):
    """Where a proactive suggestion may originate (closed set)."""

    SCHEDULED = "SCHEDULED"
    SYSTEM_CONDITION = "SYSTEM_CONDITION"
    TASK_COMPLETED = "TASK_COMPLETED"
    RESOURCE_CONDITION = "RESOURCE_CONDITION"
    REPEATED_PATTERN = "REPEATED_PATTERN"
    USER_EVENT = "USER_EVENT"


class ProposalState(str, Enum):
    """Lifecycle of one proactive proposal."""

    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


class ProactiveTrigger(Entity):
    """A watched condition (data; proposals cannot create these)."""

    kind: TriggerKind = TriggerKind.SCHEDULED
    subject: str = ""
    summary: str = ""
    cooldown_s: float = 3600.0
    enabled: bool = True


class ProactiveProposal(Entity):
    """One suggestion awaiting approval (or auto-approval if eligible)."""

    trigger_id: str = ""
    subject: str = ""
    summary: str = ""
    risk: LearningRisk = LearningRisk.LOW
    state: ProposalState = ProposalState.PROPOSED


class ProactiveConfig(Entity):
    """Operator controls for proactivity."""

    enabled: bool = True
    quiet_start_hour: int = 22
    quiet_end_hour: int = 7
    scope: str = ""
    allowed_categories: list[str] = []
    max_per_hour: int = 5
    require_approval: bool = True


class ProactiveEngine:
    """Evaluates triggers into approved, bounded, verifiable actions."""

    def __init__(
        self,
        config: ProactiveConfig | None = None,
        bus: EventBus | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config or ProactiveConfig()
        self._bus = bus
        self._clock = clock or time.time
        self._triggers: dict[str, ProactiveTrigger] = {}
        self._proposals: dict[str, ProactiveProposal] = {}
        self._last_fired: dict[tuple[str, str], float] = {}
        self._proposal_times: list[float] = []

    def add_trigger(self, trigger: ProactiveTrigger) -> ProactiveTrigger:
        """Register a trigger (operator/config action, not a proposal)."""
        self._triggers[trigger.id] = trigger
        return trigger

    def remove_trigger(self, trigger_id: str) -> bool:
        """Remove a trigger."""
        return self._triggers.pop(trigger_id, None) is not None

    def evaluate(self, trigger_id: str, subject: str = "",
                 risk: LearningRisk = LearningRisk.LOW,
                 summary: str = "") -> ProactiveProposal | None:
        """Turn one fired trigger into a proposal (or None: suppressed)."""
        trigger = self._triggers.get(trigger_id)
        if trigger is None or not self._config.enabled or not trigger.enabled:
            return None
        if trigger.kind.value not in (self._config.allowed_categories or
                                      [k.value for k in TriggerKind]):
            return None
        if self._in_quiet_hours():
            return None
        now = self._clock()
        key = (trigger_id, subject)
        if now - self._last_fired.get(key, 0.0) < trigger.cooldown_s:
            return None  # duplicate within cooldown
        self._proposal_times = [t for t in self._proposal_times if now - t < 3600.0]
        if len(self._proposal_times) >= self._config.max_per_hour:
            return None  # frequency cap applies to proposals, not just runs
        proposal = ProactiveProposal(
            trigger_id=trigger_id, subject=subject,
            summary=summary or trigger.summary, risk=risk)
        self._proposals[proposal.id] = proposal
        self._proposal_times.append(now)
        self._last_fired[key] = now
        self._emit(EventType.PROACTIVE_TRIGGERED, "", {
            "trigger_id": trigger_id, "proposal_id": proposal.id})
        if not self._config.require_approval and risk is LearningRisk.LOW:
            proposal.state = ProposalState.APPROVED
        return proposal

    def approve(self, proposal_id: str) -> ProactiveProposal:
        """Explicitly approve a proposal."""
        proposal = self._require(proposal_id)
        if proposal.state is not ProposalState.PROPOSED:
            raise DomainValidationError("proposal already decided")
        proposal.state = ProposalState.APPROVED
        return proposal

    def reject(self, proposal_id: str) -> ProactiveProposal:
        """Explicitly reject a proposal."""
        proposal = self._require(proposal_id)
        if proposal.state is not ProposalState.PROPOSED:
            raise DomainValidationError("proposal already decided")
        proposal.state = ProposalState.REJECTED
        return proposal

    def execute(self, proposal_id: str, execute_fn: Callable[[], Any],
                verify_fn: Callable[[Any], bool] | None = None) -> ProactiveProposal:
        """Run an approved proposal through injected callables."""
        proposal = self._require(proposal_id)
        if proposal.state is not ProposalState.APPROVED:
            raise DomainValidationError("only approved proposals execute")
        try:
            outcome = execute_fn()
        except Exception as exc:  # noqa: BLE001 -- proposal fails, engine stands
            proposal.state = ProposalState.FAILED
            self._emit(EventType.PROACTIVE_EXECUTED, "",
                       {"proposal_id": proposal.id, "success": False,
                        "error": str(exc)})
            return proposal
        verified = verify_fn(outcome) if verify_fn is not None else True
        proposal.state = (ProposalState.EXECUTED if verified
                          else ProposalState.FAILED)
        self._emit(EventType.PROACTIVE_EXECUTED, "",
                   {"proposal_id": proposal.id, "success": verified})
        return proposal

    def pending(self) -> list[ProactiveProposal]:
        """Proposals awaiting a decision."""
        return [p for p in self._proposals.values()
                if p.state is ProposalState.PROPOSED]

    def _in_quiet_hours(self) -> bool:
        import time as _time

        # UTC hour: the injected clock is epoch seconds, so gmtime keeps
        # quiet hours aligned regardless of operator timezone.
        hour = _time.gmtime(self._clock()).tm_hour
        start, end = self._config.quiet_start_hour, self._config.quiet_end_hour
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    def _require(self, proposal_id: str) -> ProactiveProposal:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise DomainValidationError(f"unknown proposal {proposal_id!r}")
        return proposal

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(event_type=event_type, task_id=task_id, payload=payload))
