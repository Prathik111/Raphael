"""Escalation: terminal handoff to a human/operator (Gate 10)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.base import utcnow
from ai_ecosystem.core.models.enums import EventType


@dataclass
class Escalation:
    """One recorded handoff with its evidence."""

    task_id: str = ""
    reason: str = ""
    evidence: list[str] = field(default_factory=list)
    created_at: Any = None

    def __post_init__(self) -> None:
        if self.created_at is None:
            self.created_at = utcnow()


class EscalationManager:
    """Records escalations and announces them on the event bus."""

    def __init__(self, bus: EventBus | None = None) -> None:
        self._bus = bus
        self.escalations: list[Escalation] = []

    def escalate(
        self, task_id: str, reason: str, evidence: list[str] | None = None
    ) -> Escalation:
        """Record an escalation and emit RECOVERY_EXHAUSTED."""
        escalation = Escalation(
            task_id=task_id, reason=reason, evidence=list(evidence or [])
        )
        self.escalations.append(escalation)
        if self._bus is not None:
            self._bus.publish(
                Event(
                    event_type=EventType.RECOVERY_EXHAUSTED,
                    task_id=task_id,
                    payload={"reason": reason, "evidence": escalation.evidence},
                )
            )
        return escalation
