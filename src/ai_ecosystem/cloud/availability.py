"""PC availability via heartbeats and leases (Gate 25).

Presence is not control: knowing a PC is ONLINE never authorizes
anything on it, and presence rows carry no personal information --
only an opaque device id, capabilities, and coarse pressure.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import Field

from ai_ecosystem.core.errors.exceptions import (
    DomainValidationError,
    ResourceNotFoundError,
)
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.base import Entity, utcnow
from ai_ecosystem.core.models.enums import EventType


class PCStatus(str, Enum):
    """Presence states for the primary PC."""

    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    SLEEPING = "SLEEPING"
    UNREACHABLE = "UNREACHABLE"
    BUSY = "BUSY"
    AVAILABLE = "AVAILABLE"


class DevicePresence(Entity):
    """One device's presence row (opaque id, no personal data)."""

    device_id: str = ""
    last_seen: datetime = Field(default_factory=utcnow)
    status: PCStatus = PCStatus.OFFLINE
    capabilities: list[str] = Field(default_factory=list)
    resource_pressure: str = "UNKNOWN"
    network_reachable: bool = False
    agent_runtime: str = "unknown"


_KNOWN_PRESSURES = frozenset({"LOW", "MODERATE", "HIGH", "CRITICAL", "UNKNOWN"})
_MAX_CAPABILITIES = 100
_MAX_CAPABILITY_CHARS = 256


def _check_heartbeat(device_id: str, capabilities: Optional[list[str]],
                     pressure: str, runtime: str) -> list[str]:
    """Validate heartbeat shape (presence carries no personal data)."""
    if not device_id:
        raise DomainValidationError("device_id must not be empty")
    if len(device_id) > 256:
        raise DomainValidationError("device_id too long")
    caps = list(capabilities or [])
    if len(caps) > _MAX_CAPABILITIES:
        raise DomainValidationError("too many capabilities")
    for capability in caps:
        if not isinstance(capability, str) or len(capability) > _MAX_CAPABILITY_CHARS:
            raise DomainValidationError("capability entries must be short strings")
    if pressure not in _KNOWN_PRESSURES:
        raise DomainValidationError(f"unknown pressure {pressure!r}")
    if len(runtime) > 256:
        raise DomainValidationError("runtime identifier too long")
    return caps


class PCAvailabilityService:
    """Heartbeat/lease tracker over the existing database."""

    def __init__(self, repository: Any, bus: Optional[EventBus] = None,
                 lease_timeout_s: float = 60.0) -> None:
        if lease_timeout_s <= 0:
            raise DomainValidationError("lease_timeout_s must be positive")
        self._repo = repository
        self._bus = bus
        self._lease_timeout_s = lease_timeout_s

    def heartbeat(self, device_id: str, status: PCStatus = PCStatus.ONLINE,
                  capabilities: Optional[list[str]] = None,
                  pressure: str = "UNKNOWN", reachable: bool = True,
                  runtime: str = "unknown",
                  now: Optional[datetime] = None) -> DevicePresence:
        """Record presence (idempotent: duplicates just refresh the lease)."""
        caps = _check_heartbeat(device_id, capabilities, pressure, runtime)
        moment = now or utcnow()
        existing = self._find(device_id)
        if existing is None:
            presence = DevicePresence(
                device_id=device_id, last_seen=moment, status=status,
                capabilities=caps,
                resource_pressure=pressure, network_reachable=reachable,
                agent_runtime=runtime)
            created = self._repo.create(presence)
            self._emit(EventType.PC_ONLINE, {"device_id": device_id})
            return created
        previous = existing.status
        existing.last_seen = moment
        existing.status = status
        existing.capabilities = caps if capabilities is not None else existing.capabilities
        existing.resource_pressure = pressure
        existing.network_reachable = reachable
        existing.agent_runtime = runtime
        existing.touch()
        updated = self._repo.update(existing)
        if previous in (PCStatus.OFFLINE, PCStatus.UNREACHABLE) and status in (
                PCStatus.ONLINE, PCStatus.AVAILABLE):
            self._emit(EventType.PC_ONLINE, {"device_id": device_id})
        return updated

    def set_status(self, device_id: str, status: PCStatus) -> DevicePresence:
        """Explicit operator transition (sleeping/busy/available)."""
        presence = self._require(device_id)
        presence.status = status
        presence.touch()
        return self._repo.update(presence)

    def get(self, device_id: str) -> DevicePresence:
        """Fetch one device (raises when unknown)."""
        return self._require(device_id)

    def is_available(self, device_id: str, now: Optional[datetime] = None) -> bool:
        """True for fresh ONLINE/AVAILABLE rows (lease-checked)."""
        presence = self._find(device_id)
        if presence is None:
            return False
        if presence.status not in (PCStatus.ONLINE, PCStatus.AVAILABLE):
            return False
        return not self._expired(presence, now or utcnow())

    def poll(self, now: Optional[datetime] = None) -> list[DevicePresence]:
        """Expire stale leases (no aggressive polling thread involved)."""
        moment = now or utcnow()
        expired = []
        for presence in self._repo.list():
            if presence.status is PCStatus.OFFLINE:
                continue
            if self._expired(presence, moment):
                presence.status = PCStatus.OFFLINE
                presence.touch()
                self._repo.update(presence)
                expired.append(presence)
                self._emit(EventType.PC_EXPIRED, {"device_id": presence.device_id})
        return expired

    def mark_offline(self, device_id: str) -> DevicePresence:
        """Record an observed disconnection."""
        presence = self._require(device_id)
        presence.status = PCStatus.OFFLINE
        presence.network_reachable = False
        presence.touch()
        updated = self._repo.update(presence)
        self._emit(EventType.PC_OFFLINE, {"device_id": device_id})
        return updated

    def _expired(self, presence: DevicePresence, now: datetime) -> bool:
        age = (now - presence.last_seen).total_seconds()
        return age > self._lease_timeout_s

    def _find(self, device_id: str) -> Optional[DevicePresence]:
        for presence in self._repo.list():
            if presence.device_id == device_id:
                return presence
        return None

    def _require(self, device_id: str) -> DevicePresence:
        presence = self._find(device_id)
        if presence is None:
            raise ResourceNotFoundError("DevicePresence", device_id)
        return presence

    def _emit(self, event_type: EventType, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(Event(event_type=event_type, payload=payload))


class SqlitePresenceRepository:
    """Presence rows in the existing database (no new database)."""

    def __init__(self, db: Any) -> None:
        from ai_ecosystem.core.persistence.sqlite import _SnapshotTable

        self._t = _SnapshotTable(db, "device_presence", DevicePresence)

    def create(self, item: DevicePresence) -> DevicePresence:
        """Persist a presence row."""
        return self._t.create(item)

    def get(self, item_id: str) -> Optional[DevicePresence]:
        """Fetch by record id."""
        return self._t.get(item_id)

    def update(self, item: DevicePresence) -> DevicePresence:
        """Replace the stored row."""
        return self._t.update(item)

    def delete(self, item_id: str) -> bool:
        """Remove a row."""
        return self._t.delete(item_id)

    def list(self) -> list[DevicePresence]:
        """All rows."""
        return self._t.list()


def presence_age_s(presence: DevicePresence, now: Optional[datetime] = None) -> float:
    """Seconds since the last heartbeat (lease math helper)."""
    moment = now or utcnow()
    return max(0.0, (moment - presence.last_seen).total_seconds())
