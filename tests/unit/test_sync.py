"""Gate 24: sync is explicit, idempotent, resumable, and secret-proof."""

import time

import pytest

from ai_ecosystem.cloud import (
    MockSyncTransport,
    SyncClass,
    SyncInProgressError,
    SyncManager,
    SyncPolicy,
    SyncState,
    make_sync_object,
)
from ai_ecosystem.core.errors import DomainValidationError
from ai_ecosystem.core.events import Event, EventBus
from ai_ecosystem.core.models.enums import EventType


def _obj(object_id="task:1", object_type="task", payload=None, version="v1"):
    return make_sync_object(object_type, object_id,
                            payload if payload is not None else {"state": "CREATED"},
                            version=version)


def test_1_manifest_creation():
    manager = SyncManager()
    manifest = manager.manifest([_obj(), _obj("skill:1", "skill")])
    assert [(m.object_id, m.object_type) for m in manifest] == [
        ("task:1", "task"), ("skill:1", "skill")]
    assert all(m.content_hash for m in manifest)
    assert manifest[0].sync_class is SyncClass.SYNC_ALLOWED


def test_2_allowed_object():
    manager = SyncManager(transport=MockSyncTransport())
    report = manager.sync([_obj()])
    assert report.results[0].state is SyncState.UPLOADED


def test_3_forbidden_object():
    manager = SyncManager(
        policy=SyncPolicy(overrides={"task:1": SyncClass.SYNC_FORBIDDEN}),
        transport=MockSyncTransport())
    report = manager.sync([_obj()])
    assert report.results[0].state is SyncState.SKIPPED
    assert manager._transport.remote == {}


def test_4_policy_filtering():
    policy = SyncPolicy(defaults={"task": SyncClass.LOCAL_ONLY})
    assert policy.classify(_obj()) is SyncClass.LOCAL_ONLY
    assert policy.classify(_obj("x", "mystery")) is SyncClass.LOCAL_ONLY


def test_5_upload():
    transport = MockSyncTransport()
    SyncManager(transport=transport).sync([_obj()])
    assert "task:1" in transport.remote


def test_6_download():
    transport = MockSyncTransport()
    remote = _obj("task:9", payload={"state": "RUNNING"}, version="v2")
    transport.remote["task:9"] = remote
    manager = SyncManager(transport=transport)
    report = manager.sync([])
    assert report.results[0].state is SyncState.DOWNLOADED
    assert report.results[0].object_id == "task:9"


def test_7_noop_synchronization():
    transport = MockSyncTransport()
    manager = SyncManager(transport=transport)
    manager.sync([_obj()])
    report = manager.sync([_obj()])
    assert report.results[0].state is SyncState.IN_SYNC
    assert transport.pushes == 1


def test_8_idempotency():
    transport = MockSyncTransport()
    manager = SyncManager(transport=transport)
    objects = [_obj()]
    for _ in range(3):
        report = manager.sync(objects)
    assert transport.pushes == 1  # uploaded once, then version-matched
    assert report.results[0].state is SyncState.IN_SYNC


def test_9_version_comparison():
    transport = MockSyncTransport()
    manager = SyncManager(transport=transport)
    manager.sync([_obj(version="v1")])
    report = manager.sync([_obj(version="v2")])  # local moved on
    assert report.results[0].state is SyncState.UPLOADED
    assert transport.pushes == 2


def test_10_conflict():
    transport = MockSyncTransport()
    manager = SyncManager(transport=transport)
    manager.sync([_obj(version="v1")])
    # Remote changes behind our back (different version+hash, no handshake).
    transport.remote["task:1"] = _obj(version="v9",
                                      payload={"state": "REMOTE-EDIT"})
    report = manager.sync([_obj(version="v2")])
    assert report.results[0].state is SyncState.CONFLICT
    # Nothing silently overwritten either way.
    assert transport.remote["task:1"].version == "v9"


def test_11_conflict_resolution():
    transport = MockSyncTransport()
    manager = SyncManager(transport=transport)
    manager.sync([_obj(version="v1")])
    transport.remote["task:1"] = _obj(version="v9", payload={"state": "X"})
    manager.sync([_obj(version="v2")])
    kept = manager.resolve("task:1", "local", [_obj(version="v2")])
    assert kept.state is SyncState.UPLOADED
    assert transport.remote["task:1"].version == "v2"
    with pytest.raises(DomainValidationError):
        manager.resolve("task:1", "sideways", [_obj()])


def test_12_interrupted_synchronization():
    transport = MockSyncTransport(fail_after_pushes=1)
    manager = SyncManager(transport=transport)
    first = manager.sync([_obj("a", payload={"n": 1}), _obj("b", payload={"n": 2})])
    assert first.results[0].state is SyncState.UPLOADED
    assert first.results[1].state is SyncState.FAILED  # died mid-run
    transport.fail_after_pushes = -1  # network back, resume
    second = manager.sync([_obj("a", payload={"n": 1}), _obj("b", payload={"n": 2})])
    assert [r.state for r in second.results] == [SyncState.IN_SYNC, SyncState.UPLOADED]
    assert len(transport.remote) == 2  # no duplicates, no loss


def test_13_network_failure():
    manager = SyncManager(transport=MockSyncTransport(down=True))
    report = manager.sync([_obj()])
    assert report.state is SyncState.FAILED
    assert report.results[0].state is SyncState.FAILED


def test_14_retry_and_15_retry_limit():
    transport = MockSyncTransport(down=True)
    manager = SyncManager(transport=transport, max_retries=2)
    report = manager.retry_sync([_obj()])
    assert report.state is SyncState.FAILED
    assert report.attempts == 3  # initial + 2 bounded retries, then stop


def test_16_credential_redaction():
    leaked = make_sync_object("task", "task:1",
                              {"state": "x", "api_key": "SECRET-1",
                               "nested": {"password": "SECRET-2"}})
    manager = SyncManager(policy=SyncPolicy(), transport=MockSyncTransport())
    assert manager.policy.classify(leaked) is SyncClass.SYNC_FORBIDDEN
    report = manager.sync([leaked])
    assert report.results[0].state is SyncState.SKIPPED


def test_17_secret_filtering():
    assert SyncPolicy().classify(
        make_sync_object("memory", "m:1", {"token": "abc"})) is SyncClass.SYNC_FORBIDDEN
    allowed = make_sync_object("memory", "m:1", {"content": "plain summary"})
    assert SyncPolicy().classify(allowed) is SyncClass.SYNC_RESTRICTED


def test_18_restart_recovery(tmp_path):
    import json

    cursor = tmp_path / "cursor.json"
    transport = MockSyncTransport()
    manager = SyncManager(transport=transport)
    manager.sync([_obj()])
    # Persist the acknowledged cursor; a new process resumes from it.
    cursor.write_text(json.dumps(manager._acknowledged))
    fresh = SyncManager(transport=transport)
    fresh._acknowledged = dict(json.loads(cursor.read_text()))
    report = fresh.sync([_obj()])
    assert report.results[0].state is SyncState.IN_SYNC
    assert transport.pushes == 1


def test_19_concurrent_sync_protection():
    manager = SyncManager(transport=MockSyncTransport())
    manager._lock.acquire()
    try:
        with pytest.raises(SyncInProgressError):
            manager.sync([_obj()])
    finally:
        manager._lock.release()
    assert manager.sync([_obj()]).results[0].state is SyncState.UPLOADED


def test_20_audit_events():
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe_all(seen.append)
    manager = SyncManager(transport=MockSyncTransport(), bus=bus)
    manager.sync([_obj()])
    manager.sync([_obj("task:1", payload={"state": "CHANGED"}, version="v2")])
    kinds = [e.event_type for e in seen]
    assert EventType.SYNC_STARTED in kinds
    assert EventType.SYNC_OBJECT_UPLOADED in kinds
    assert EventType.SYNC_COMPLETED in kinds
    for event in seen:
        assert "api_key" not in str(event.payload).lower()


def test_sync_perf_smoke():
    transport = MockSyncTransport()
    manager = SyncManager(transport=transport)
    objects = [make_sync_object("task", f"task:{i}", {"state": "x"})
               for i in range(200)]
    started = time.monotonic()
    manifest = manager.manifest(objects)
    report = manager.sync(objects)
    elapsed = time.monotonic() - started
    print(f"\nsync smoke: 200-object manifest+sync in {elapsed:.2f}s")
    assert len(manifest) == 200
    assert all(r.state is SyncState.UPLOADED for r in report.results)
    assert elapsed < 5
