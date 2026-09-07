"""Profile persistence over the existing SQLite store (Gate 13)."""

from __future__ import annotations

from typing import Optional

from ai_ecosystem.core.errors.exceptions import ResourceNotFoundError
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.enums import EventType, MemoryScope
from ai_ecosystem.core.persistence.sqlite import Database, _SnapshotTable
from ai_ecosystem.personalization.personality.profiles import (
    PersonalityProfile,
    PreferenceProfile,
)


class PersonalityStore:
    """Single active personality profile (versioned, evented)."""

    def __init__(self, db: Database, bus: Optional[EventBus] = None) -> None:
        self._table = _SnapshotTable(db, "personalities", PersonalityProfile)
        self._bus = bus

    def get(self) -> PersonalityProfile:
        """Active profile, or the default when none was saved."""
        profiles = self._table.list()
        if not profiles:
            return PersonalityProfile()
        return sorted(profiles, key=lambda p: p.version)[-1]

    def save(self, profile: PersonalityProfile) -> PersonalityProfile:
        """Persist a new version (bumped) of the personality."""
        profile.version = self.get().version + 1 if self._table.list() else 1
        profile.touch()
        created = self._table.create(profile)
        self._emit(EventType.PERSONALITY_UPDATED, "",
                   {"profile_id": created.id, "version": created.version})
        return created

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(event_type=event_type, task_id=task_id, payload=payload)
            )


class PreferenceStore:
    """Global + per-project preference profiles with override merge."""

    def __init__(self, db: Database, bus: Optional[EventBus] = None) -> None:
        self._table = _SnapshotTable(db, "preferences", PreferenceProfile)
        self._bus = bus

    def get_global(self) -> PreferenceProfile:
        """Latest global profile, or a default when none was saved."""
        candidates = [p for p in self._table.list() if p.scope is MemoryScope.GLOBAL]
        if not candidates:
            return PreferenceProfile()
        return sorted(candidates, key=lambda p: p.version)[-1]

    def get_project(self, project_id: str) -> Optional[PreferenceProfile]:
        """Latest project profile, or None when the project has none."""
        candidates = [
            p for p in self._table.list()
            if p.scope is MemoryScope.PROJECT and p.scope_id == project_id
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda p: p.version)[-1]

    def save(self, profile: PreferenceProfile) -> PreferenceProfile:
        """Persist a preference profile (new version per save)."""
        existing = [
            p for p in self._table.list()
            if p.scope is profile.scope and p.scope_id == profile.scope_id
        ]
        profile.version = max([p.version for p in existing], default=0) + 1
        profile.touch()
        created = self._table.create(profile)
        self._emit(EventType.PREFERENCE_UPDATED, "",
                   {"profile_id": created.id, "scope": created.scope.value,
                    "scope_id": created.scope_id, "version": created.version})
        return created

    def get_effective(self, project_id: str = "") -> PreferenceProfile:
        """Global merged with project overrides (project wins per field).

        A project field overrides only when it differs from a fresh
        default, so untouched project fields inherit the global value.
        """
        base = self.get_global()
        if not project_id:
            return base
        override = self.get_project(project_id)
        if override is None:
            return base
        fresh = PreferenceProfile()
        merged = base.model_copy(deep=True)
        if override.preferred_workflows != fresh.preferred_workflows:
            merged.preferred_workflows = list(override.preferred_workflows)
        if override.preferred_tools != fresh.preferred_tools:
            merged.preferred_tools = list(override.preferred_tools)
        if override.output_format != fresh.output_format:
            merged.output_format = override.output_format
        merged.defaults = {**base.defaults, **override.defaults}
        merged.scope_id = project_id
        return merged

    def get(self, profile_id: str) -> PreferenceProfile:
        """Fetch by id (raises when unknown)."""
        profile = self._table.get(profile_id)
        if profile is None:
            raise ResourceNotFoundError("PreferenceProfile", profile_id)
        return profile

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(event_type=event_type, task_id=task_id, payload=payload)
            )
