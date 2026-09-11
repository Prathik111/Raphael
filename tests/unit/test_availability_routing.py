"""Gates 25-26: presence leases + deterministic policy routing."""

from datetime import timedelta

import pytest

from ai_ecosystem.cloud import (
    ComputePolicy,
    ComputeRequirements,
    ComputeRouter,
    DevicePresence,
    NoRouteError,
    PCAvailabilityService,
    PCStatus,
    PolicyRejectionError,
    ProviderCapabilities,
    SqlitePresenceRepository,
)
from ai_ecosystem.core.errors import DomainValidationError, ResourceNotFoundError
from ai_ecosystem.core.events import Event, EventBus
from ai_ecosystem.core.models.base import utcnow
from ai_ecosystem.core.models.enums import EventType
from ai_ecosystem.core.persistence import Database


@pytest.fixture()
def service():
    db = Database(":memory:")
    db.migrate()
    bus = EventBus()
    yield (
        PCAvailabilityService(SqlitePresenceRepository(db), bus, lease_timeout_s=60.0),
        bus,
        db,
    )
    db.close()


def _providers(healthy=None):
    healthy = {"local": True, "oci": True, "kaggle": True} | (healthy or {})
    return {
        "local": ProviderCapabilities(
            name="local",
            local=True,
            ram_gb=16.0,
            runtimes=["python"],
            models=["mock"],
            cost_per_hour=0.0,
            latency_class="fast",
            reliability=0.99,
        ),
        "oci": ProviderCapabilities(
            name="oci",
            ram_gb=64.0,
            gpu=True,
            vram_gb=40.0,
            runtimes=["python", "cuda"],
            models=["oci-mock"],
            cost_per_hour=2.0,
            latency_class="standard",
            reliability=0.999,
        ),
        "kaggle": ProviderCapabilities(
            name="kaggle",
            ram_gb=32.0,
            gpu=True,
            vram_gb=16.0,
            runtimes=["python", "cuda"],
            cost_per_hour=0.0,
            latency_class="slow",
            reliability=0.9,
        ),
    }, healthy


def _router(healthy=None, policy=None):
    providers, health = _providers(healthy)
    return ComputeRouter(providers, availability=health.get, policy=policy)


def test_heartbeat(service):
    svc, _, _ = service
    presence = svc.heartbeat("pc-1", capabilities=["gpu"])
    assert presence.status is PCStatus.ONLINE
    assert presence.capabilities == ["gpu"]
    assert svc.is_available("pc-1") is True


def test_expiration(service):
    svc, bus, _ = service
    seen: list[Event] = []
    bus.subscribe_all(seen.append)
    old = utcnow() - timedelta(seconds=120)
    svc.heartbeat("pc-1", now=old)
    assert svc.is_available("pc-1") is False
    expired = svc.poll()
    assert [p.device_id for p in expired] == ["pc-1"]
    assert svc.get("pc-1").status is PCStatus.OFFLINE
    assert EventType.PC_EXPIRED in [e.event_type for e in seen]


def test_reconnect(service):
    svc, _, _ = service
    svc.heartbeat("pc-1", now=utcnow() - timedelta(seconds=120))
    svc.poll()
    assert svc.get("pc-1").status is PCStatus.OFFLINE
    svc.heartbeat("pc-1")  # reconnect refreshes the lease
    assert svc.is_available("pc-1") is True


def test_offline_transition(service):
    svc, _, _ = service
    svc.heartbeat("pc-1")
    svc.mark_offline("pc-1")
    assert svc.get("pc-1").status is PCStatus.OFFLINE
    assert svc.is_available("pc-1") is False


def test_duplicate_heartbeat(service):
    svc, _, _ = service
    first = svc.heartbeat("pc-1")
    second = svc.heartbeat("pc-1")
    assert first.id == second.id  # idempotent: one row refreshed


def test_stale_heartbeat(service):
    svc, _, _ = service
    svc.heartbeat(
        "pc-1", status=PCStatus.SLEEPING, now=utcnow() - timedelta(seconds=3600)
    )
    assert svc.is_available("pc-1") is False  # sleeping is not available
    assert svc.poll()[0].status is PCStatus.OFFLINE


def test_network_failure(service):
    svc, _, _ = service
    svc.heartbeat("pc-1", reachable=False)
    assert svc.get("pc-1").network_reachable is False
    assert svc.is_available("pc-1") is True  # reachable flag is informational


def test_restart(tmp_path):
    path = str(tmp_path / "presence.db")
    first = Database(path)
    first.migrate()
    PCAvailabilityService(SqlitePresenceRepository(first)).heartbeat("pc-1")
    first.close()
    second = Database(path)
    second.migrate()
    try:
        svc = PCAvailabilityService(SqlitePresenceRepository(second))
        assert svc.is_available("pc-1") is True
    finally:
        second.close()


def test_deterministic_state_transitions(service):
    svc, _, _ = service
    with pytest.raises(ResourceNotFoundError):
        svc.get("ghost")
    with pytest.raises(DomainValidationError):
        svc.heartbeat("")
    svc.heartbeat("pc-1")
    assert svc.set_status("pc-1", PCStatus.BUSY).status is PCStatus.BUSY
    assert svc.is_available("pc-1") is False  # busy is not routable


def test_presence_carries_no_personal_data(service):
    svc, _, _ = service
    presence = svc.heartbeat("pc-1")
    dumped = presence.model_dump_json().lower()
    for forbidden in ("hostname", "username", "password", "token"):
        assert forbidden not in dumped
    assert isinstance(presence, DevicePresence)


def test_local_selection():
    router = _router()
    target = router.route(ComputeRequirements(ram_gb=8.0, runtime="python"))
    assert target.provider == "local"  # free and fast wins


def test_oci_selection():
    router = _router()
    target = router.route(
        ComputeRequirements(gpu=True, vram_gb=32.0, runtime="cuda", model="oci-mock")
    )
    assert target.provider == "oci"  # only OCI has 32GB+ VRAM


def test_unavailable_provider():
    router = _router({"local": False, "oci": True, "kaggle": False})
    target = router.route(ComputeRequirements(ram_gb=8.0))
    assert target.provider == "oci"


def test_insufficient_vram():
    router = _router()
    with pytest.raises(NoRouteError):
        router.route(ComputeRequirements(gpu=True, vram_gb=999.0))


def test_insufficient_ram():
    router = _router()
    with pytest.raises(NoRouteError):
        router.route(ComputeRequirements(ram_gb=9999.0))


def test_privacy_restriction():
    router = _router({"local": True})
    with pytest.raises(PolicyRejectionError):
        # High privacy + incapable local PC must fail, never leak to cloud.
        router.route(ComputeRequirements(privacy="high", gpu=True, vram_gb=80.0))
    ok = _router({"local": True}).route(ComputeRequirements(privacy="high", ram_gb=4.0))
    assert ok.provider == "local"


def test_fallback():
    router = _router()
    ranked = router.rank(ComputeRequirements(gpu=True, vram_gb=8.0, duration_s=3600.0))
    assert [t.provider for t in ranked] == ["kaggle", "oci"]
    chosen = router.select_first_available(ranked, lambda name: name != "kaggle")
    assert chosen.provider == "oci"
    with pytest.raises(NoRouteError):
        router.select_first_available(ranked, lambda name: False)


def test_provider_failure():
    def flaky(name):
        raise ConnectionError("health endpoint down")

    router = ComputeRouter(_providers()[0], availability=flaky)
    with pytest.raises(NoRouteError):
        router.route(ComputeRequirements())


def test_deterministic_routing():
    first = _router().rank(ComputeRequirements(gpu=True))
    second = _router().rank(ComputeRequirements(gpu=True))
    assert [(t.provider, t.estimated_cost) for t in first] == [
        (t.provider, t.estimated_cost) for t in second
    ]


def test_policy_rejection():
    router = _router(policy=ComputePolicy(blocked_providers=["local"]))
    target = router.route(ComputeRequirements(ram_gb=4.0))
    assert target.provider != "local"
    cheap_only = ComputeRouter(
        _providers()[0], policy=ComputePolicy(max_cost_per_hour=0.0)
    )
    assert cheap_only.route(ComputeRequirements()).provider in ("local", "kaggle")
