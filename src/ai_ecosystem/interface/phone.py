"""Phone as a thin control/status client (Gate 35).

The phone holds a session token, never credentials and never policy.
Reads go straight to the runtime API; deny cancels the task (a real,
safe effect); approve records an auditable acknowledgment that grants
nothing -- authorization stays fully automatic. No camera, no audio
capture, no local execution on either end.
"""

from __future__ import annotations

import secrets as secrets_lib
from typing import Any, Optional

from ai_ecosystem.core.errors.exceptions import DomainValidationError
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.enums import EventType
from ai_ecosystem.interface.api import ApiError, RuntimeAPI
from ai_ecosystem.interface.gateway import EcosystemGateway


class PhoneClient:
    """Authenticated thin client over a RuntimeAPI instance."""

    def __init__(self, gateway: EcosystemGateway, api: RuntimeAPI,
                 bus: Optional[EventBus] = None) -> None:
        self._gateway = gateway
        self._api = api
        self._bus = bus

    def pair(self, code: str) -> str:
        """Join with a pairing code (device secret generated locally)."""
        return self._gateway.pair(code, secrets_lib.token_hex(16))

    def status(self, token: str) -> dict[str, Any]:
        """Tasks + agents + awareness in one read-only view."""
        self._gateway.check(token, "status", nonce=_nonce())
        try:
            awareness = self._api.get_system_awareness()
        except ApiError:
            awareness = {"unavailable": True}
        return {"tasks": self._api.list_tasks(),
                "agents": self._api.get_agent_status(),
                "awareness": awareness}

    def permission_requests(self, token: str, limit: int = 20) -> list[dict]:
        """Recent permission decisions (read-only log)."""
        self._gateway.check(token, "permissions-view", nonce=_nonce())
        return self._api.recent_permission_events(limit)

    def deny(self, token: str, task_id: str) -> dict[str, Any]:
        """Deny = cancel the task (safe, immediate, audited)."""
        session = self._gateway.check(token, "cancel", nonce=_nonce())
        cancelled = self._api.cancel_task(task_id)
        self._emit(EventType.PHONE_DECISION, task_id,
                   {"device_id": session.device_id, "decision": "deny"})
        return cancelled

    def approve(self, token: str, task_id: str, call_id: str) -> dict[str, Any]:
        """Acknowledge a request WITHOUT granting anything.

        Recorded for the audit trail only: authorization remains fully
        automatic, so a phone approval can never permit an action the
        policy would deny. The call_id is required so the audit entry
        references a real request instead of a phantom approval.
        """
        if not call_id:
            raise DomainValidationError("approve requires a call_id")
        session = self._gateway.check(token, "decide", nonce=_nonce())
        self._emit(EventType.PHONE_DECISION, task_id,
                   {"device_id": session.device_id, "decision": "approve",
                    "call_id": call_id,
                    "note": "acknowledgment only; grants nothing"})
        return {"acknowledged": True, "grants": "nothing"}

    def submit_voice_goal(self, token: str, text: str) -> dict[str, Any]:
        """Voice transcripts enter as ordinary goals (Gate 37 hook)."""
        self._gateway.check(token, "tasks", nonce=_nonce())
        if not text or not text.strip():
            raise DomainValidationError("voice transcript is empty")
        return self._api.create_task(text.strip()[:4000])

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(event_type=event_type, task_id=task_id, payload=payload))


def _nonce() -> str:
    return secrets_lib.token_hex(8)
