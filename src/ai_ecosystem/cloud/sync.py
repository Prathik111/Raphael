"""PC <-> OCI synchronization of explicitly eligible metadata (Gate 24).

Only structured metadata synchronizes -- never file contents, secrets,
or credentials. Every object carries a classification; the conservative
default is LOCAL_ONLY. Sync is idempotent (version-keyed), resumable
after interruption, conflict-explicit (never silent overwrite), and
bounded in retries. The local runtime never blocks on sync.
"""

from __future__ import annotations

import hashlib
import json
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any
from collections.abc import Callable

from pydantic import BaseModel, Field

from ai_ecosystem.core.errors.exceptions import AiEcosystemError, DomainValidationError
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.base import utcnow
from ai_ecosystem.core.models.enums import EventType
from ai_ecosystem.core.secrets import looks_secret


class SyncClass(str, Enum):
    """Eligibility of one object for synchronization."""

    LOCAL_ONLY = "LOCAL_ONLY"
    SYNC_ALLOWED = "SYNC_ALLOWED"
    SYNC_RESTRICTED = "SYNC_RESTRICTED"
    SYNC_FORBIDDEN = "SYNC_FORBIDDEN"


class SyncState(str, Enum):
    """Outcome states for objects and runs."""

    IN_SYNC = "IN_SYNC"
    UPLOADED = "UPLOADED"
    DOWNLOADED = "DOWNLOADED"
    SKIPPED = "SKIPPED"
    CONFLICT = "CONFLICT"
    FAILED = "FAILED"
    BUSY = "BUSY"
    COMPLETED = "COMPLETED"


class SyncInProgressError(AiEcosystemError):
    """A sync run was requested while another holds the lock."""


class SyncObject(BaseModel):
    """One syncable metadata envelope (content already minimized)."""

    object_id: str = ""
    object_type: str = ""
    version: str = ""
    content_hash: str = ""
    scope: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utcnow)


class ManifestEntry(BaseModel):
    """Manifest row: identity + version without the payload."""

    object_id: str = ""
    object_type: str = ""
    version: str = ""
    content_hash: str = ""
    scope: str = ""
    sync_class: SyncClass = SyncClass.LOCAL_ONLY
    timestamp: datetime = Field(default_factory=utcnow)


class SyncResult(BaseModel):
    """Per-object outcome (auditable, content-free)."""

    object_id: str = ""
    object_type: str = ""
    state: SyncState = SyncState.SKIPPED
    detail: str = ""


class SyncReport(BaseModel):
    """Whole-run outcome."""

    state: SyncState = SyncState.COMPLETED
    results: list[SyncResult] = Field(default_factory=list)
    attempts: int = 1
    error: str = ""


def canonical_hash(payload: dict[str, Any]) -> str:
    """Deterministic content hash (sorted keys, compact separators)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def contains_secrets(payload: Any) -> bool:
    """True for secret-smelling keys OR known credential value formats."""
    from ai_ecosystem.core.secrets import looks_like_secret_value

    if isinstance(payload, dict):
        return any(
            looks_secret(str(key)) or contains_secrets(value) for key, value in payload.items()
        )
    if isinstance(payload, list):
        return any(contains_secrets(item) for item in payload)
    return looks_like_secret_value(payload)


_DEFAULT_CLASS: dict[str, SyncClass] = {
    "task": SyncClass.SYNC_ALLOWED,
    "agent": SyncClass.SYNC_ALLOWED,
    "workspace": SyncClass.SYNC_ALLOWED,
    "skill": SyncClass.SYNC_ALLOWED,
    "model": SyncClass.SYNC_ALLOWED,
    "job": SyncClass.SYNC_ALLOWED,
    "memory": SyncClass.SYNC_RESTRICTED,  # only cloud_eligible, content-free
    "event": SyncClass.SYNC_ALLOWED,  # counts/metadata, never payloads
}


class SyncPolicy:
    """Classification with per-object overrides (conservative default)."""

    def __init__(
        self,
        defaults: dict[str, SyncClass] | None = None,
        overrides: dict[str, SyncClass] | None = None,
        allow_restricted_download: bool = False,
    ) -> None:
        self._defaults = dict(defaults or _DEFAULT_CLASS)
        self._overrides = dict(overrides or {})
        self.allow_restricted_download = allow_restricted_download

    def classify(self, obj: SyncObject) -> SyncClass:
        """Eligibility for one object (secrets force FORBIDDEN)."""
        if contains_secrets(obj.payload):
            return SyncClass.SYNC_FORBIDDEN
        if obj.object_id in self._overrides:
            return self._overrides[obj.object_id]
        return self._defaults.get(obj.object_type, SyncClass.LOCAL_ONLY)

    def can_upload(self, obj: SyncObject) -> bool:
        """Uploadable classes (RESTRICTED needs no extra flag to upload)."""
        return self.classify(obj) in (SyncClass.SYNC_ALLOWED, SyncClass.SYNC_RESTRICTED)

    def can_download(self, sync_class: SyncClass) -> bool:
        """Downloads are stricter: RESTRICTED needs explicit opt-in."""
        if sync_class is SyncClass.SYNC_ALLOWED:
            return True
        return sync_class is SyncClass.SYNC_RESTRICTED and self.allow_restricted_download


class SyncTransport(ABC):
    """Remote side of sync (mocked in tests, OCI-backed later)."""

    @abstractmethod
    def list_versions(self) -> dict[str, tuple[str, str]]:
        """object_id -> (version, content_hash) known remotely."""
        raise NotImplementedError

    @abstractmethod
    def push(self, obj: SyncObject) -> str:
        """Store remotely; return the acknowledged version."""
        raise NotImplementedError

    @abstractmethod
    def pull(self, object_id: str) -> SyncObject | None:
        """Fetch one remote object (None when absent)."""
        raise NotImplementedError


class MockSyncTransport(SyncTransport):
    """In-memory remote with scripted failures and call accounting."""

    def __init__(self, down: bool = False, fail_after_pushes: int = -1) -> None:
        self.down = down
        self.fail_after_pushes = fail_after_pushes
        self.remote: dict[str, SyncObject] = {}
        self.pushes = 0
        self.pulls = 0

    def _check(self) -> None:
        if self.down:
            raise ConnectionError("mock network is down")

    def list_versions(self) -> dict[str, tuple[str, str]]:
        """Remote version index (empty when unreachable -> raises)."""
        self._check()
        return {oid: (obj.version, obj.content_hash) for oid, obj in self.remote.items()}

    def push(self, obj: SyncObject) -> str:
        """Store; optionally die after N pushes (interruption drills)."""
        self._check()
        if 0 <= self.fail_after_pushes <= self.pushes:
            raise ConnectionError("mock network died mid-sync")
        self.pushes += 1
        self.remote[obj.object_id] = obj.model_copy(deep=True)
        return obj.version

    def pull(self, object_id: str) -> SyncObject | None:
        """Fetch a copy (None when absent)."""
        self._check()
        self.pulls += 1
        found = self.remote.get(object_id)
        return found.model_copy(deep=True) if found else None


class SyncManager:
    """Idempotent, resumable, conflict-explicit synchronization."""

    def __init__(
        self,
        policy: SyncPolicy | None = None,
        transport: SyncTransport | None = None,
        bus: EventBus | None = None,
        max_retries: int = 3,
    ) -> None:
        self._policy = policy or SyncPolicy()
        self._transport = transport or MockSyncTransport()
        self._bus = bus
        self._max_retries = max(0, max_retries)
        self._lock = threading.Lock()
        self._running = False
        self._acknowledged: dict[str, str] = {}  # object_id -> version

    @property
    def policy(self) -> SyncPolicy:
        """Active policy (the transport can never change it)."""
        return self._policy

    def manifest(self, objects: list[SyncObject]) -> list[ManifestEntry]:
        """Build the version manifest for local objects."""
        return [
            ManifestEntry(
                object_id=obj.object_id,
                object_type=obj.object_type,
                version=obj.version,
                content_hash=obj.content_hash,
                scope=obj.scope,
                sync_class=self._policy.classify(obj),
                timestamp=obj.updated_at,
            )
            for obj in objects
        ]

    def sync(self, objects: list[SyncObject]) -> SyncReport:
        """One bounded run: upload, download, conflicts -- never overwrite."""
        if not self._lock.acquire(blocking=False):
            raise SyncInProgressError("a sync run is already in progress")
        try:
            self._running = True
            return self._run(objects)
        finally:
            self._running = False
            self._lock.release()

    def retry_sync(self, objects: list[SyncObject]) -> SyncReport:
        """Bounded retries around sync() (network failures only)."""
        attempts = 0
        while True:
            attempts += 1
            try:
                report = self.sync(objects)
            except SyncInProgressError:
                raise
            except (ConnectionError, OSError) as exc:
                if attempts > self._max_retries:
                    return SyncReport(
                        state=SyncState.FAILED,
                        attempts=attempts,
                        error=f"retry budget exhausted: {exc}",
                    )
                continue
            if report.state is SyncState.FAILED and attempts <= self._max_retries:
                continue
            report.attempts = attempts
            return report

    def resolve(self, object_id: str, keep: str, objects: list[SyncObject]) -> SyncResult:
        """Resolve a conflict explicitly (keep 'local' or 'remote').

        Explicit resolution still passes policy: forbidden objects can
        never be pushed, and restricted downloads need opt-in.
        """
        if keep not in ("local", "remote"):
            raise DomainValidationError("keep must be 'local' or 'remote'")
        local = next((o for o in objects if o.object_id == object_id), None)
        if local is None:
            raise DomainValidationError(f"unknown object {object_id!r}")
        try:
            self._transport.list_versions()
        except (ConnectionError, OSError) as exc:
            return SyncResult(
                object_id=object_id,
                object_type=local.object_type,
                state=SyncState.FAILED,
                detail=str(exc),
            )
        if keep == "local":
            if not self._policy.can_upload(local):
                return SyncResult(
                    object_id=object_id,
                    object_type=local.object_type,
                    state=SyncState.SKIPPED,
                    detail=f"policy {self._policy.classify(local).value}: " "upload not permitted",
                )
            try:
                version = self._transport.push(local)
            except (ConnectionError, OSError) as exc:
                return SyncResult(
                    object_id=object_id,
                    object_type=local.object_type,
                    state=SyncState.FAILED,
                    detail=str(exc),
                )
            self._acknowledged[object_id] = version
            return SyncResult(
                object_id=object_id,
                object_type=local.object_type,
                state=SyncState.UPLOADED,
                detail="conflict kept local",
            )
        remote = self._transport.pull(object_id)
        if remote is None:
            return SyncResult(
                object_id=object_id,
                object_type=local.object_type,
                state=SyncState.FAILED,
                detail="remote object vanished",
            )
        if not self._policy.can_download(self._policy.classify(remote)):
            return SyncResult(
                object_id=object_id,
                object_type=local.object_type,
                state=SyncState.SKIPPED,
                detail="download not permitted by policy",
            )
        self._acknowledged[object_id] = remote.version
        return SyncResult(
            object_id=object_id,
            object_type=local.object_type,
            state=SyncState.DOWNLOADED,
            detail="conflict kept remote",
        )

    # -- internals ----------------------------------------------------------

    def _run(self, objects: list[SyncObject]) -> SyncReport:
        self._emit(EventType.SYNC_STARTED, "", {"objects": len(objects)})
        try:
            remote_versions = self._transport.list_versions()
        except (ConnectionError, OSError) as exc:
            return self._failed(objects, f"remote unreachable: {exc}")
        results: list[SyncResult] = []
        for obj in objects:
            results.append(self._sync_one(obj, remote_versions))
        results.extend(self._pull_new(objects, remote_versions))
        failed = [r for r in results if r.state is SyncState.FAILED]
        state = (
            SyncState.FAILED
            if failed
            and not [
                r
                for r in results
                if r.state in (SyncState.UPLOADED, SyncState.DOWNLOADED, SyncState.IN_SYNC)
            ]
            else SyncState.COMPLETED
        )
        report = SyncReport(state=state, results=results)
        self._emit(
            EventType.SYNC_COMPLETED,
            "",
            {
                "state": state.value,
                "up": sum(1 for r in results if r.state is SyncState.UPLOADED),
                "down": sum(1 for r in results if r.state is SyncState.DOWNLOADED),
                "conflicts": sum(1 for r in results if r.state is SyncState.CONFLICT),
            },
        )
        return report

    def _sync_one(self, obj: SyncObject, remote_versions: dict[str, tuple[str, str]]) -> SyncResult:
        sync_class = self._policy.classify(obj)
        if sync_class in (SyncClass.LOCAL_ONLY, SyncClass.SYNC_FORBIDDEN):
            return self._record(obj, SyncState.SKIPPED, f"policy {sync_class.value}")
        if not self._policy.can_upload(obj):
            return self._record(obj, SyncState.SKIPPED, "upload not permitted")
        remote = remote_versions.get(obj.object_id)
        if remote is None:
            return self._push(obj, "new remote object")
        remote_version, remote_hash = remote
        if remote_version == obj.version and remote_hash == obj.content_hash:
            self._acknowledged[obj.object_id] = obj.version
            return self._record(obj, SyncState.IN_SYNC, "versions match")
        if self._acknowledged.get(obj.object_id) == remote_version:
            # Remote matches what we last pushed: local moved on -> upload.
            return self._push(obj, "local newer")
        # Both sides moved since the last handshake: explicit conflict.
        return self._record(
            obj, SyncState.CONFLICT, f"local v{obj.version} vs remote v{remote_version}"
        )

    def _push(self, obj: SyncObject, detail: str) -> SyncResult:
        try:
            version = self._transport.push(obj)
        except (ConnectionError, OSError) as exc:
            return self._record(obj, SyncState.FAILED, f"upload failed: {exc}")
        self._acknowledged[obj.object_id] = version
        return self._record(obj, SyncState.UPLOADED, detail)

    def _pull_new(
        self, objects: list[SyncObject], remote_versions: dict[str, tuple[str, str]]
    ) -> list[SyncResult]:
        local_ids = {obj.object_id for obj in objects}
        results = []
        for object_id, (version, _hash) in sorted(remote_versions.items()):
            if object_id in local_ids or self._acknowledged.get(object_id) == version:
                continue
            remote = self._transport.pull(object_id)
            if remote is None:
                continue
            sync_class = self._policy.classify(remote)
            if not self._policy.can_download(sync_class):
                results.append(
                    SyncResult(
                        object_id=object_id,
                        object_type=remote.object_type,
                        state=SyncState.SKIPPED,
                        detail="download not permitted",
                    )
                )
                continue
            self._acknowledged[object_id] = version
            results.append(
                SyncResult(
                    object_id=object_id,
                    object_type=remote.object_type,
                    state=SyncState.DOWNLOADED,
                    detail="new remote object",
                )
            )
        for result in results:
            self._emit_for(result)
        return results

    def _failed(self, objects: list[SyncObject], error: str) -> SyncReport:
        results = [
            SyncResult(
                object_id=o.object_id,
                object_type=o.object_type,
                state=SyncState.FAILED,
                detail=error,
            )
            for o in objects
        ]
        for result in results:
            self._emit_for(result)
        self._emit(EventType.SYNC_FAILED, "", {"error": error})
        return SyncReport(state=SyncState.FAILED, results=results, error=error)

    def _record(self, obj: SyncObject, state: SyncState, detail: str) -> SyncResult:
        result = SyncResult(
            object_id=obj.object_id, object_type=obj.object_type, state=state, detail=detail
        )
        self._emit_for(result)
        return result

    def _emit_for(self, result: SyncResult) -> None:
        mapping = {
            SyncState.UPLOADED: EventType.SYNC_OBJECT_UPLOADED,
            SyncState.DOWNLOADED: EventType.SYNC_OBJECT_DOWNLOADED,
            SyncState.CONFLICT: EventType.SYNC_CONFLICT,
            SyncState.SKIPPED: EventType.SYNC_SKIPPED,
            SyncState.FAILED: EventType.SYNC_FAILED,
            SyncState.IN_SYNC: EventType.SYNC_SKIPPED,
        }
        self._emit(
            mapping.get(result.state, EventType.SYNC_SKIPPED),
            "",
            {
                "object_id": result.object_id,
                "object_type": result.object_type,
                "detail": result.detail,
            },
        )

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(Event(event_type=event_type, task_id=task_id, payload=payload))


def make_sync_object(
    object_type: str, object_id: str, payload: dict, scope: str = "", version: str = ""
) -> SyncObject:
    """Build a versioned, hashed envelope for metadata payloads."""
    content_hash = canonical_hash(payload)
    return SyncObject(
        object_id=object_id,
        object_type=object_type,
        version=version or content_hash[:12],
        content_hash=content_hash,
        scope=scope,
        payload=dict(payload),
    )


SyncAdapter = Callable[[], list[SyncObject]]
