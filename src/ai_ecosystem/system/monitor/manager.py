"""SystemAwarenessManager: explicit, one-shot awareness (Gate 15).

There is deliberately no background monitoring: every snapshot comes
from an explicit ``snapshot()`` call. ``enabled=False`` refuses even
that, so usage observation (Gate 16) can never silently piggyback.
"""

from __future__ import annotations


from ai_ecosystem.core.errors.exceptions import AiEcosystemError
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.enums import EventType
from ai_ecosystem.system.monitor.models import (
    AwarenessContext,
    Capabilities,
    PressureLevel,
    SystemSnapshot,
)
from ai_ecosystem.system.monitor.probe import SystemProbe

_CAPABILITY_FIELDS = [name for name in Capabilities.model_fields]


class AwarenessDisabledError(AiEcosystemError):
    """Awareness was requested while explicitly disabled."""


def build_context(snapshot: SystemSnapshot) -> AwarenessContext:
    """Answer planning questions from a snapshot (pure function)."""
    available = [name for name in _CAPABILITY_FIELDS if getattr(snapshot.capabilities, name)]
    unavailable = [name for name in _CAPABILITY_FIELDS if not getattr(snapshot.capabilities, name)]
    constrained = []
    cpu = snapshot.cpu.utilization_percent
    memory = snapshot.memory.utilization_percent
    if cpu is not None and cpu >= 85.0:
        constrained.append("cpu")
    if memory is not None and memory >= 85.0:
        constrained.append("memory")
    for volume in snapshot.storage:
        if volume.utilization_percent is not None and volume.utilization_percent >= 90.0:
            constrained.append(f"storage:{volume.mount}")
    for gpu in snapshot.gpus:
        if gpu.utilization_percent is not None and gpu.utilization_percent >= 90.0:
            constrained.append(f"gpu:{gpu.name or 'device'}")
    under_pressure = snapshot.pressure in (PressureLevel.HIGH, PressureLevel.CRITICAL)
    summary = (
        f"{snapshot.operating_system or 'unknown OS'}; "
        f"pressure {snapshot.pressure.value}; "
        f"{len(available)} capabilities available, "
        f"{len(unavailable)} unavailable"
        + (f"; constrained: {', '.join(constrained)}" if constrained else "")
    )
    return AwarenessContext(
        can_compute_locally=not under_pressure and bool(snapshot.cpu.logical_processors),
        available_capabilities=available,
        unavailable_capabilities=unavailable,
        constrained_resources=constrained,
        pressure=snapshot.pressure,
        summary=summary,
    )


class SystemAwarenessManager:
    """One-shot snapshots + contexts, gated by an explicit enabled flag."""

    def __init__(
        self,
        probe: SystemProbe,
        bus: EventBus | None = None,
        enabled: bool = True,
        include_host: bool = False,
    ) -> None:
        self._probe = probe
        self._bus = bus
        self._enabled = enabled
        self._include_host = include_host

    @property
    def enabled(self) -> bool:
        """Whether awareness collection is currently allowed."""
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """Operator control for awareness collection."""
        self._enabled = enabled

    def snapshot(self) -> SystemSnapshot:
        """Take one snapshot (refused when disabled)."""
        if not self._enabled:
            raise AwarenessDisabledError("system awareness is disabled")
        self._emit(EventType.SYSTEM_AWARENESS_REQUESTED, {})
        snap = self._probe.snapshot(include_host=self._include_host)
        # Event payload carries summaries, never process lists or hostnames.
        self._emit(
            EventType.SYSTEM_SNAPSHOT_CREATED,
            {
                "os": snap.operating_system,
                "arch": snap.architecture,
                "pressure": snap.pressure.value,
                "capabilities": [n for n in _CAPABILITY_FIELDS if getattr(snap.capabilities, n)],
                "processes": len(snap.processes),
            },
        )
        for name in _CAPABILITY_FIELDS:
            if getattr(snap.capabilities, name):
                self._emit(EventType.CAPABILITY_DETECTED, {"capability": name})
        if snap.pressure in (PressureLevel.HIGH, PressureLevel.CRITICAL):
            self._emit(EventType.RESOURCE_PRESSURE_DETECTED, {"pressure": snap.pressure.value})
        return snap

    def context(self) -> AwarenessContext:
        """Snapshot plus planner-ready answers in one call."""
        return build_context(self.snapshot())

    def _emit(self, event_type: EventType, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(Event(event_type=event_type, payload=payload))
