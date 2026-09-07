"""Gate 15: system awareness -- all hardware via deterministic mocks."""

import time

import pytest

from ai_ecosystem.core.events import Event, EventBus
from ai_ecosystem.core.models.enums import EventType
from ai_ecosystem.core.persistence import (
    Database,
    SqliteSnapshotRepository,
)
from ai_ecosystem.system.monitor import (
    AwarenessDisabledError,
    SystemAwarenessManager,
    build_context,
    classify_pressure,
)
from ai_ecosystem.system.monitor.models import (
    Capabilities,
    CpuInfo,
    GpuInfo,
    MemoryInfo,
    PressureLevel,
    ProcessInfo,
    StorageVolume,
    SystemSnapshot,
)
from ai_ecosystem.system.monitor.probe import LocalSystemProbe, MockProbe
from ai_ecosystem.system.monitor.tools import awareness_tools
from ai_ecosystem.tools import GrantAllAuthorizer, ToolRegistry, ToolRunner


def _rich_snapshot(**overrides):
    args = {
        "operating_system": "TestOS 1.0",
        "architecture": "x86_64",
        "cpu": CpuInfo(model="Test CPU", logical_processors=8,
                       utilization_percent=42.0),
        "memory": MemoryInfo(total_bytes=16_000_000_000,
                             available_bytes=8_000_000_000,
                             used_bytes=8_000_000_000,
                             utilization_percent=50.0),
        "storage": [StorageVolume(mount="/", total_bytes=1000,
                                  available_bytes=600,
                                  utilization_percent=40.0)],
        "processes": [ProcessInfo(name="agent", cpu_percent=5.0)],
        "capabilities": Capabilities(git_available=True, python_available=True,
                                     storage_available=True),
        "pressure": PressureLevel.LOW,
    }
    args.update(overrides)
    return SystemSnapshot(**args)


def test_1_snapshot_creation():
    manager = SystemAwarenessManager(MockProbe(_rich_snapshot()))
    snap = manager.snapshot()
    assert snap.operating_system == "TestOS 1.0"
    assert snap.architecture == "x86_64"
    assert snap.collected_at is not None
    assert snap.hostname == ""  # host identity off by default


def test_2_cpu_information():
    snap = MockProbe(_rich_snapshot()).snapshot()
    assert snap.cpu.model == "Test CPU"
    assert snap.cpu.logical_processors == 8
    assert snap.cpu.utilization_percent == 42.0


def test_3_memory_information():
    snap = MockProbe(_rich_snapshot()).snapshot()
    assert snap.memory.total_bytes == 16_000_000_000
    assert snap.memory.available_bytes == 8_000_000_000
    assert snap.memory.utilization_percent == 50.0


def test_4_storage_information():
    snap = MockProbe(_rich_snapshot()).snapshot()
    assert len(snap.storage) == 1
    assert snap.storage[0].mount == "/"
    assert snap.storage[0].utilization_percent == 40.0


def test_5_gpu_information_mocked():
    gpu = GpuInfo(name="Mock GPU", vram_total_bytes=12_000_000_000,
                  vram_available_bytes=4_000_000_000, utilization_percent=66.0)
    snap = MockProbe(_rich_snapshot(gpus=[gpu])).snapshot()
    assert len(snap.gpus) == 1
    assert snap.gpus[0].vram_total_bytes == 12_000_000_000
    assert snap.gpus[0].utilization_percent == 66.0


def test_6_capability_detection():
    assert classify_pressure(None, None, None) is PressureLevel.UNKNOWN
    probe = LocalSystemProbe(checkers={
        "git_available": lambda: True,
        "docker_available": lambda: False,
        "network_available": lambda: True,
    })
    snap = probe.snapshot()
    assert snap.capabilities.git_available is True
    assert snap.capabilities.docker_available is False
    assert snap.capabilities.network_available is True
    assert snap.capabilities.python_available is True


def test_7_unavailable_capability():
    snap = MockProbe(_rich_snapshot()).snapshot()
    assert snap.capabilities.gpu_available is False
    assert snap.capabilities.docker_available is False
    context = build_context(snap)
    assert "gpu_available" in context.unavailable_capabilities
    assert "docker_available" in context.unavailable_capabilities


def test_8_resource_pressure_classification():
    assert classify_pressure(95.0, 10.0, 10.0) is PressureLevel.CRITICAL
    assert classify_pressure(10.0, 90.0, 10.0) is PressureLevel.HIGH
    assert classify_pressure(10.0, 10.0, 75.0) is PressureLevel.MODERATE
    assert classify_pressure(10.0, 10.0, 10.0) is PressureLevel.LOW
    pressured = MockProbe(_rich_snapshot(
        cpu=CpuInfo(model="x", logical_processors=4, utilization_percent=91.0),
        pressure=PressureLevel.HIGH)).snapshot()
    context = build_context(pressured)
    assert context.pressure is PressureLevel.HIGH
    assert "cpu" in context.constrained_resources
    assert context.can_compute_locally is False


def test_9_awareness_context_generation():
    context = build_context(_rich_snapshot())
    assert context.can_compute_locally is True
    assert "git_available" in context.available_capabilities
    assert context.constrained_resources == []
    assert "TestOS" in context.summary


def test_10_disabled_monitoring():
    manager = SystemAwarenessManager(MockProbe(_rich_snapshot()), enabled=False)
    assert manager.enabled is False
    with pytest.raises(AwarenessDisabledError):
        manager.snapshot()
    assert manager._probe.calls == 0  # nothing collected while disabled
    manager.set_enabled(True)
    assert manager.snapshot().operating_system == "TestOS 1.0"


def test_11_no_sensitive_data_collection():
    snap = MockProbe(_rich_snapshot()).snapshot()
    dumped = snap.model_dump_json().lower()
    for forbidden in ("password", "token", "secret", "keystroke", "camera",
                      "microphone", "cookie"):
        assert forbidden not in dumped
    for process in snap.processes:
        assert process.model_dump() == {"name": process.name,
                                        "cpu_percent": process.cpu_percent,
                                        "memory_bytes": process.memory_bytes}


def test_12_persistence_and_13_restart(tmp_path):
    path = str(tmp_path / "sys.db")
    first = Database(path)
    first.migrate()
    repo = SqliteSnapshotRepository(first)
    created = repo.create(_rich_snapshot())
    assert repo.latest().id == created.id
    first.close()
    second = Database(path)
    second.migrate()
    try:
        latest = SqliteSnapshotRepository(second).latest()
    finally:
        second.close()
    assert latest is not None
    assert latest.operating_system == "TestOS 1.0"
    assert latest.cpu.logical_processors == 8


def test_awareness_events_carry_summaries_not_raw_data():
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe_all(seen.append)
    manager = SystemAwarenessManager(MockProbe(_rich_snapshot()), bus=bus)
    manager.snapshot()
    kinds = [e.event_type for e in seen]
    assert kinds[0] is EventType.SYSTEM_AWARENESS_REQUESTED
    assert EventType.SYSTEM_SNAPSHOT_CREATED in kinds
    assert EventType.CAPABILITY_DETECTED in kinds
    created = next(e for e in seen
                   if e.event_type is EventType.SYSTEM_SNAPSHOT_CREATED)
    assert "processes" in created.payload  # count only...
    assert created.payload["processes"] == 1  # ...never the list itself
    assert "hostname" not in created.payload


def test_pressure_event_on_high_load():
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe_all(seen.append)
    manager = SystemAwarenessManager(MockProbe(_rich_snapshot(
        pressure=PressureLevel.CRITICAL)), bus=bus)
    manager.snapshot()
    assert EventType.RESOURCE_PRESSURE_DETECTED in [e.event_type for e in seen]


def test_awareness_tool_through_runner():
    manager = SystemAwarenessManager(MockProbe(_rich_snapshot()))
    registry = ToolRegistry()
    for tool, handler in awareness_tools(manager):
        registry.register(tool, handler)
    runner = ToolRunner(registry, GrantAllAuthorizer())
    result = runner.run(registry.build_call("t", "system.snapshot", {}))
    assert result.success
    assert result.output["pressure"] == "LOW"
    assert "processes" not in result.output  # redacted
    assert "hostname" not in result.output


def test_snapshot_perf_smoke():
    manager = SystemAwarenessManager(MockProbe(_rich_snapshot()))
    started = time.monotonic()
    for _ in range(100):
        manager.snapshot()
    elapsed = time.monotonic() - started
    print(f"\nawareness smoke: 100 snapshots in {elapsed:.2f}s")
    assert elapsed < 5
