"""Authenticated device gateway shared by phone and hardware (Gates 35-36).

Pairing is a ceremony, not a copied static secret: the gateway issues
single-use short-lived codes; the device answers with a fresh secret
it generated, which becomes the pairing credential. Sessions are
expiring tokens bound to scopes; every state-changing call carries a
nonce inside a replay window. Revocation is immediate.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_lib
import secrets as secrets_lib
import time
from collections import deque
from collections.abc import Callable

from ai_ecosystem.core.errors.exceptions import AiEcosystemError, DomainValidationError
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.enums import EventType

PHONE_SCOPES = frozenset({"status", "tasks", "cancel", "permissions-view", "awareness", "decide"})
HARDWARE_SCOPES = frozenset({"status", "approve-record", "deny-cancel", "stop", "model-note"})

# Scopes with side effects require a fresh nonce (replay protection is
# mandatory where a replayed call would do something twice).
_WRITE_SCOPES = frozenset({"tasks", "cancel", "decide"})


class GatewayError(AiEcosystemError):
    """Pairing/auth failure (messages name reasons, never secrets)."""


class DeviceSession:
    """One live session: device, role scopes, and expiry."""

    def __init__(
        self, token: str, device_id: str, role: str, scopes: frozenset, expires_at: float
    ) -> None:
        self.token = token
        self.device_id = device_id
        self.role = role
        self.scopes = scopes
        self.expires_at = expires_at


class EcosystemGateway:
    """Pairing, sessions, replay protection for thin device clients."""

    def __init__(
        self,
        bus: EventBus | None = None,
        clock: Callable[[], float] | None = None,
        code_ttl_s: float = 300.0,
        session_ttl_s: float = 3600.0,
        replay_window_s: float = 300.0,
    ) -> None:
        self._bus = bus
        self._clock = clock or time.time
        self._code_ttl = code_ttl_s
        self._session_ttl = session_ttl_s
        self._replay_window = replay_window_s
        self._codes: dict[str, dict] = {}  # code -> {device_id, role, expires}
        self._secrets: dict[str, str] = {}  # device_id -> pairing secret
        self._sessions: dict[str, DeviceSession] = {}
        self._revoked_devices: set[str] = set()
        self._nonces: deque[tuple[str, float]] = deque()

    # -- pairing ----------------------------------------------------------

    def issue_pairing_code(self, device_id: str, role: str) -> str:
        """Create a single-use code (operator reads it to the device)."""
        if role not in ("phone", "hardware"):
            raise DomainValidationError(f"unknown device role {role!r}")
        self._gc()
        code = secrets_lib.token_hex(16)  # 128-bit pairing codes
        self._codes[code] = {
            "device_id": device_id,
            "role": role,
            "expires": self._clock() + self._code_ttl,
            "used": False,
        }
        return code

    def pair(self, code: str, device_secret: str) -> str:
        """Redeem a code with a device-generated secret; returns a token."""
        entry = self._codes.get(code)
        if entry is None or entry["used"]:
            raise GatewayError("invalid or reused pairing code")
        if self._clock() > entry["expires"]:
            raise GatewayError("pairing code expired")
        if not device_secret or len(device_secret) < 16 or len(set(device_secret)) < 8:
            raise GatewayError("device secret too weak")
        entry["used"] = True
        device_id = entry["device_id"]
        self._secrets[device_id] = device_secret
        self._revoked_devices.discard(device_id)
        token = self._mint(device_id, entry["role"])
        self._emit(EventType.DEVICE_PAIRED, "", {"device_id": device_id, "role": entry["role"]})
        return token

    # -- sessions ------------------------------------------------------------

    def refresh(self, token: str) -> str:
        """Exchange a live token for a fresh one (old token revoked)."""
        session = self._live_session(token)
        self._sessions.pop(token, None)
        return self._mint(session.device_id, session.role)

    def revoke_token(self, token: str) -> bool:
        """Revoke one session token."""
        return self._sessions.pop(token, None) is not None

    def revoke_device(self, device_id: str) -> None:
        """Revoke a device: all sessions die, secret is dropped."""
        self._sessions = {
            token: session
            for token, session in self._sessions.items()
            if session.device_id != device_id
        }
        self._secrets.pop(device_id, None)
        self._revoked_devices.add(device_id)
        self._emit(EventType.DEVICE_REVOKED, "", {"device_id": device_id})

    def check(self, token: str, scope: str, nonce: str = "") -> DeviceSession:
        """Authenticate + authorize one call (expiry, revocation, replay).

        State-changing scopes require a fresh nonce; without one the
        call is rejected even with a valid token.
        """
        session = self._live_session(token)
        if scope not in session.scopes:
            raise GatewayError(f"scope {scope!r} not granted to {session.role}")
        if scope in _WRITE_SCOPES and not nonce:
            raise GatewayError("state-changing calls require a nonce")
        if nonce:
            self._use_nonce(nonce)
        return session

    def device_secret(self, device_id: str) -> str | None:
        """Pairing secret for HMAC verification (server-side only)."""
        return self._secrets.get(device_id)

    # -- helpers ---------------------------------------------------------------

    def _live_session(self, token: str) -> DeviceSession:
        session = self._sessions.get(token)
        if session is None:
            raise GatewayError("unknown session token")
        if session.device_id in self._revoked_devices:
            raise GatewayError("device revoked")
        if self._clock() > session.expires_at:
            self._sessions.pop(token, None)
            raise GatewayError("session expired")
        return session

    def _mint(self, device_id: str, role: str) -> str:
        token = secrets_lib.token_hex(16)
        scopes = PHONE_SCOPES if role == "phone" else HARDWARE_SCOPES
        self._sessions[token] = DeviceSession(
            token, device_id, role, scopes, self._clock() + self._session_ttl
        )
        return token

    def _use_nonce(self, nonce: str) -> None:
        now = self._clock()
        while self._nonces and now - self._nonces[0][1] > self._replay_window:
            self._nonces.popleft()
        if any(known == nonce for known, _ in self._nonces):
            raise GatewayError("replayed request rejected")
        self._nonces.append((nonce, now))

    def _gc(self) -> None:
        """Drop used/expired codes and dead sessions (bounded state)."""
        now = self._clock()
        self._codes = {
            code: entry
            for code, entry in self._codes.items()
            if not entry["used"] and entry["expires"] > now
        }
        self._sessions = {
            token: session
            for token, session in self._sessions.items()
            if session.expires_at > now and session.device_id not in self._revoked_devices
        }

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(Event(event_type=event_type, task_id=task_id, payload=payload))


def hmac_sign(secret: str, canonical: str) -> str:
    """HMAC-SHA256 signature for device packets."""
    return hmac_lib.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def hmac_check(secret: str, canonical: str, signature: str) -> bool:
    """Constant-time signature verification."""
    return hmac_lib.compare_digest(hmac_sign(secret, canonical), signature)
