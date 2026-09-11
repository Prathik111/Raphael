"""Hardware console protocol for ESP32-class devices (Gate 36).

Versioned, authenticated, bounded packets over any transport: the
device signs canonical bytes with its pairing secret; the gateway side
checks version, signature, freshness, and replay before mapping the
command. Dangerous controls (stop/kill/deny) cancel tasks -- a real,
safe effect. Approve is an audited acknowledgment, never a grant.
Model/project switches are recorded intents, never silent reconfig.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ai_ecosystem.core.errors.exceptions import DomainValidationError
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.enums import EventType
from ai_ecosystem.interface.api import RuntimeAPI
from ai_ecosystem.interface.gateway import EcosystemGateway, hmac_check

PROTOCOL_VERSION = "1.0"
SUPPORTED_MAJOR = "1"
MAX_PACKET_BYTES = 4_096
REPLAY_WINDOW_S = 300.0

HARDWARE_COMMANDS = frozenset(
    {"status", "approve", "deny", "stop", "kill", "model-note", "project-note"}
)


class DevicePacket(BaseModel):
    """One wire packet (transport-agnostic dict form)."""

    protocol_version: str = PROTOCOL_VERSION
    device_id: str = ""
    command: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: float = 0.0
    nonce: str = ""
    signature: str = ""


class SimulatedDevice:
    """In-process stand-in for firmware (tests + simulator UI)."""

    def __init__(self, device_id: str, secret: str, clock: Any = None) -> None:
        import time as _time

        self.device_id = device_id
        self._secret = secret
        self._clock = clock or _time.time
        self._counter = 0

    def packet(
        self,
        command: str,
        payload: dict | None = None,
        protocol_version: str = PROTOCOL_VERSION,
    ) -> dict:
        """Build and sign a packet dict."""
        import json as _json

        from ai_ecosystem.interface.gateway import hmac_sign

        self._counter += 1
        body = {
            "protocol_version": protocol_version,
            "device_id": self.device_id,
            "command": command,
            "payload": payload or {},
            "timestamp": self._clock(),
            "nonce": f"{self.device_id}-{self._counter}",
        }
        canonical = _json.dumps(body, sort_keys=True, separators=(",", ":"))
        body["signature"] = hmac_sign(self._secret, canonical)
        return body


class HardwareGateway:
    """Verifies packets and maps commands to safe runtime effects."""

    def __init__(
        self,
        gateway: EcosystemGateway,
        api: RuntimeAPI,
        bus: EventBus | None = None,
        clock: Any = None,
    ) -> None:
        import time as _time

        self._gateway = gateway
        self._api = api
        self._bus = bus
        self._clock = clock or _time.time
        self._seen_nonces: dict[str, float] = {}

    def handle(self, packet: dict) -> dict[str, Any]:
        """Validate fully, then execute the mapped safe effect."""
        message = self._parse(packet)
        self._authenticate(message)
        handler = {
            "status": self._status,
            "approve": self._approve,
            "deny": self._deny,
            "stop": self._stop,
            "kill": self._kill,
            "model-note": self._note,
            "project-note": self._note,
        }[message.command]
        return handler(message)

    # -- validation ---------------------------------------------------------

    def _parse(self, packet: dict) -> DevicePacket:
        if not isinstance(packet, dict):
            raise DomainValidationError("packet must be a mapping")
        import json as _json

        try:
            encoded = _json.dumps(packet).encode()
        except (TypeError, ValueError) as exc:
            raise DomainValidationError(f"packet not serializable: {exc}") from exc
        if len(encoded) > MAX_PACKET_BYTES:
            raise DomainValidationError("packet exceeds size limit")
        try:
            message = DevicePacket.model_validate(packet)
        except Exception as exc:
            raise DomainValidationError(f"malformed packet: {exc}") from exc
        major = str(message.protocol_version).split(".")[0]
        if major != SUPPORTED_MAJOR:
            raise DomainValidationError(
                f"protocol version {message.protocol_version!r} unsupported"
            )
        if message.command not in HARDWARE_COMMANDS:
            raise DomainValidationError(f"unknown command {message.command!r}")
        return message

    def _authenticate(self, message: DevicePacket) -> None:
        import json as _json

        secret = self._gateway.device_secret(message.device_id)
        if secret is None:
            raise DomainValidationError("device is not paired")
        body = {
            "protocol_version": message.protocol_version,
            "device_id": message.device_id,
            "command": message.command,
            "payload": message.payload,
            "timestamp": message.timestamp,
            "nonce": message.nonce,
        }
        canonical = _json.dumps(body, sort_keys=True, separators=(",", ":"))
        if not hmac_check(secret, canonical, message.signature):
            raise DomainValidationError("bad packet signature")
        now = self._clock()
        if abs(now - message.timestamp) > REPLAY_WINDOW_S:
            raise DomainValidationError("packet outside freshness window")
        if message.nonce in self._seen_nonces:
            raise DomainValidationError("replayed packet rejected")
        self._seen_nonces[message.nonce] = now
        while len(self._seen_nonces) > 10_000:
            oldest_key = min(self._seen_nonces, key=self._seen_nonces.get)
            del self._seen_nonces[oldest_key]

    # -- command effects -------------------------------------------------------

    def _status(self, message: DevicePacket) -> dict[str, Any]:
        return {"tasks": self._api.list_tasks(), "agents": self._api.get_agent_status()}

    def _approve(self, message: DevicePacket) -> dict[str, Any]:
        self._emit(
            EventType.PHONE_DECISION,
            str(message.payload.get("task_id", "")),
            {
                "device_id": message.device_id,
                "decision": "approve",
                "note": "hardware acknowledgment only; grants nothing",
            },
        )
        return {"acknowledged": True, "grants": "nothing"}

    def _deny(self, message: DevicePacket) -> dict[str, Any]:
        task_id = str(message.payload.get("task_id", ""))
        return self._cancel(task_id, message.device_id, "deny")

    def _stop(self, message: DevicePacket) -> dict[str, Any]:
        task_id = str(message.payload.get("task_id", ""))
        return self._cancel(task_id, message.device_id, "stop")

    def _kill(self, message: DevicePacket) -> dict[str, Any]:
        """Emergency stop: cancel cancellable tasks matching an explicit scope.

        Scope is required and must be at least 3 characters: an empty or
        trivially short scope would mass-cancel unrelated work.
        """
        scope = str(message.payload.get("scope", ""))
        if len(scope) < 3:
            raise DomainValidationError(
                "kill requires an explicit scope (>= 3 characters)"
            )
        cancelled = []
        for task in self._api.list_tasks():
            if scope and scope not in task["title"]:
                continue
            if not task["completed"]:
                try:
                    self._api.cancel_task(task["task_id"])
                    cancelled.append(task["task_id"])
                except Exception:  # noqa: BLE001 -- best effort per task
                    continue
        self._emit(
            EventType.PHONE_DECISION,
            "",
            {
                "device_id": message.device_id,
                "decision": "kill",
                "cancelled": cancelled,
            },
        )
        return {"cancelled": cancelled}

    def _note(self, message: DevicePacket) -> dict[str, Any]:
        # Untrusted payload: emit only its shape, never its content.
        keys = sorted(message.payload) if isinstance(message.payload, dict) else []
        self._emit(
            EventType.PHONE_DECISION,
            "",
            {
                "device_id": message.device_id,
                "decision": f"{message.command}-recorded",
                "payload_keys": keys,
            },
        )
        return {"recorded": True, "applied": False}

    def _cancel(self, task_id: str, device_id: str, decision: str) -> dict[str, Any]:
        if not task_id:
            raise DomainValidationError("command needs a task_id")
        cancelled = self._api.cancel_task(task_id)
        self._emit(
            EventType.PHONE_DECISION,
            task_id,
            {"device_id": device_id, "decision": decision},
        )
        return cancelled

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(event_type=event_type, task_id=task_id, payload=payload)
            )
