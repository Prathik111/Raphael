"""Transport-independent ecosystem envelope (Gate 38).

One envelope shape for PC, OCI, phone, hardware, and future peers:
identity, version, type, payload, timestamp, correlation, and an auth
reference (never a secret). Validation rejects unknown majors,
oversize payloads, stale timestamps, replays, and missing auth --
without knowing whether the bytes arrived over HTTP, USB, or MQTT.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from ai_ecosystem.core.errors.exceptions import AiEcosystemError
from ai_ecosystem.core.models.base import Entity

PROTOCOL_VERSION = "1.0"
SUPPORTED_MAJOR = "1"
MAX_ENVELOPE_BYTES = 262_144
SKEW_TOLERANCE_S = 300.0
SEEN_CACHE_SIZE = 10_000


class ProtocolError(AiEcosystemError):
    """Envelope rejected (version, shape, size, freshness, replay, auth)."""


class AuthRef(BaseModel):
    """Who sent this (scheme + key id; the secret stays home)."""

    scheme: str = ""
    key_id: str = ""


class EcosystemEnvelope(Entity):
    """One protocol message, transport notwithstanding."""

    protocol_version: str = PROTOCOL_VERSION
    sender: str = ""
    sender_type: str = ""
    message_type: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str = ""
    auth: AuthRef = Field(default_factory=AuthRef)


def _deny_all(envelope: EcosystemEnvelope) -> bool:
    """Default authorizer: nothing is authorized without configuration."""
    return False


class ProtocolValidator:
    """Stateless shape checks + stateful replay cache (default-deny)."""

    def __init__(
        self,
        authorize: Callable[[EcosystemEnvelope], bool] | None = None,
        clock: Any = None,
    ) -> None:
        import time as _time

        self._authorize = authorize if authorize is not None else _deny_all
        self._clock = clock or _time.time
        self._seen: dict[str, float] = {}

    def encode(self, envelope: EcosystemEnvelope) -> bytes:
        """Serialize for any transport."""
        return envelope.model_dump_json().encode("utf-8")

    def decode(self, raw: bytes) -> EcosystemEnvelope:
        """Parse and fully validate one envelope."""
        if len(raw) > MAX_ENVELOPE_BYTES:
            raise ProtocolError("envelope exceeds size limit")
        try:
            text = raw.decode("utf-8")
            envelope = EcosystemEnvelope.model_validate_json(text)
        except Exception as exc:
            raise ProtocolError(f"malformed envelope: {exc}") from exc
        self.validate(envelope)
        return envelope

    def validate(self, envelope: EcosystemEnvelope) -> EcosystemEnvelope:
        """Shape, version, freshness, replay, and authorization checks."""
        if not envelope.sender:
            raise ProtocolError("envelope has no sender")
        if not envelope.message_type:
            raise ProtocolError("envelope has no message type")
        major = str(envelope.protocol_version).split(".")[0]
        if major != SUPPORTED_MAJOR:
            raise ProtocolError(f"protocol version {envelope.protocol_version!r} incompatible")
        now = self._clock()
        created = envelope.created_at.timestamp()
        if abs(now - created) > SKEW_TOLERANCE_S:
            raise ProtocolError("envelope outside freshness window")
        if envelope.id in self._seen:
            raise ProtocolError("replayed envelope rejected")
        if not envelope.auth.scheme or not envelope.auth.key_id:
            raise ProtocolError("envelope carries no authorization context")
        if not self._authorize(envelope):
            raise ProtocolError("sender not authorized for this message")
        self._seen[envelope.id] = now
        while len(self._seen) > SEEN_CACHE_SIZE:
            oldest_key = min(self._seen.items(), key=lambda item: item[1])[0]
            del self._seen[oldest_key]
        return envelope

    def check_not_expired(self, envelope: EcosystemEnvelope) -> None:
        """Explicit freshness re-check for long-lived handlers."""
        created = envelope.created_at.timestamp()
        if abs(self._clock() - created) > SKEW_TOLERANCE_S:
            raise ProtocolError("envelope expired")


def envelope_for(
    sender: str,
    sender_type: str,
    message_type: str,
    payload: dict | None = None,
    correlation_id: str = "",
    auth: AuthRef | None = None,
) -> EcosystemEnvelope:
    """Build a well-formed envelope (timestamps handled by the model)."""
    return EcosystemEnvelope(
        sender=sender,
        sender_type=sender_type,
        message_type=message_type,
        payload=dict(payload or {}),
        correlation_id=correlation_id,
        auth=auth or AuthRef(),
    )
