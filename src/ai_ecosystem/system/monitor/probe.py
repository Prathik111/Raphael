"""System probes: stdlib-only local probe + deterministic mock (Gate 15).

The probe interface is the seam: production uses LocalSystemProbe
(stdlib only; richer numbers when psutil happens to be installed),
tests inject MockProbe. No subprocess shells, no file-content reads.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import sys
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from ai_ecosystem.core.models.base import utcnow
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


def classify_pressure(cpu: float | None, memory: float | None,
                      storage: float | None) -> PressureLevel:
    """Worst of the known signals; UNKNOWN when nothing is known."""
    known = [v for v in (cpu, memory, storage) if v is not None]
    if not known:
        return PressureLevel.UNKNOWN
    worst = max(known)
    if worst >= 95.0:
        return PressureLevel.CRITICAL
    if worst >= 85.0:
        return PressureLevel.HIGH
    if worst >= 70.0:
        return PressureLevel.MODERATE
    return PressureLevel.LOW


class SystemProbe(ABC):
    """Anything that can produce a SystemSnapshot."""

    @abstractmethod
    def snapshot(self, include_host: bool = False) -> SystemSnapshot:
        """Collect one snapshot (host identity only when asked)."""
        raise NotImplementedError


class MockProbe(SystemProbe):
    """Deterministic probe for tests (no hardware dependence)."""

    def __init__(self, snapshot: Optional[SystemSnapshot] = None) -> None:
        self._snapshot = snapshot or SystemSnapshot(
            operating_system="MockOS", architecture="x86_64")
        self.calls = 0

    def snapshot(self, include_host: bool = False) -> SystemSnapshot:
        """Return a copy of the canned snapshot."""
        self.calls += 1
        data = self._snapshot.model_dump()
        data["hostname"] = "mock-host" if include_host else ""
        data["collected_at"] = utcnow()
        return SystemSnapshot.model_validate(data)


class LocalSystemProbe(SystemProbe):
    """Best-effort stdlib probe; unknown fields stay None/empty.

    ``checkers`` maps capability names to zero-arg callables so tests
    and operators can override individual detections. ``gpu_provider``
    supplies GPU lists on machines with vendor tooling.
    """

    def __init__(
        self,
        checkers: Optional[dict[str, Callable[[], bool]]] = None,
        gpu_provider: Optional[Callable[[], list[GpuInfo]]] = None,
        network_target: Optional[tuple[str, int]] = None,
    ) -> None:
        self._checkers = dict(checkers or {})
        self._gpu_provider = gpu_provider
        self._network_target = network_target

    def snapshot(self, include_host: bool = False) -> SystemSnapshot:
        """Collect from the local machine without shelling out."""
        cpu = CpuInfo(
            model=platform.processor() or platform.machine(),
            logical_processors=os.cpu_count() or 0,
            load_average=list(os.getloadavg()) if hasattr(os, "getloadavg") else [],
        )
        memory = MemoryInfo()
        processes: list[ProcessInfo] = []
        psutil = self._psutil()
        if psutil is not None:
            try:
                cpu.utilization_percent = float(psutil.cpu_percent(interval=None))
                virtual = psutil.virtual_memory()
                memory = MemoryInfo(
                    total_bytes=int(virtual.total),
                    available_bytes=int(virtual.available),
                    used_bytes=int(virtual.used),
                    utilization_percent=float(virtual.percent),
                )
                for proc in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
                    info = proc.info
                    rss = info.get("memory_info").rss if info.get("memory_info") else 0
                    processes.append(ProcessInfo(
                        name=str(info.get("name") or "?"),
                        cpu_percent=info.get("cpu_percent"),
                        memory_bytes=int(rss or 0),
                    ))
                    if len(processes) >= 50:
                        break
            except Exception:  # noqa: BLE001 -- degraded snapshot beats no snapshot
                pass
        storage = []
        for mount in self._mounts():
            try:
                usage = shutil.disk_usage(mount)
                percent = (usage.used / usage.total * 100.0) if usage.total else None
                storage.append(StorageVolume(
                    mount=mount, total_bytes=usage.total,
                    available_bytes=usage.free,
                    utilization_percent=percent,
                ))
            except OSError:
                continue
        gpus = self._detect_gpus()
        capabilities = Capabilities(
            local_model_available=False,
            gpu_available=bool(gpus),
            cuda_available=self._check("cuda_available", self._has_nvidia_smi),
            network_available=self._check("network_available", self._has_network),
            git_available=self._check("git_available", lambda: self._which("git")),
            python_available=True,
            docker_available=self._check("docker_available", lambda: self._which("docker")),
            storage_available=bool(storage),
            cloud_connector_available=False,
        )
        worst_storage = max(
            (v.utilization_percent for v in storage if v.utilization_percent is not None),
            default=None,
        )
        return SystemSnapshot(
            operating_system=f"{platform.system()} {platform.release()}".strip(),
            architecture=platform.machine(),
            hostname=platform.node() if include_host else "",
            cpu=cpu, memory=memory, gpus=gpus, storage=storage,
            processes=processes, capabilities=capabilities,
            pressure=classify_pressure(
                cpu.utilization_percent, memory.utilization_percent, worst_storage),
            collected_at=utcnow(),
        )

    def _check(self, name: str, default: Callable[[], bool]) -> bool:
        checker = self._checkers.get(name, default)
        try:
            return bool(checker())
        except Exception:  # noqa: BLE001 -- a failed check means unavailable
            return False

    @staticmethod
    def _which(program: str) -> bool:
        return shutil.which(program) is not None

    @staticmethod
    def _has_nvidia_smi() -> bool:
        return shutil.which("nvidia-smi") is not None

    def _has_network(self) -> bool:
        if self._network_target is not None:
            host, port = self._network_target
        else:
            return False  # no implicit internet probes; operators opt in
        try:
            socket.create_connection((host, port), timeout=2).close()
            return True
        except OSError:
            return False

    @staticmethod
    def _mounts() -> list[str]:
        if os.name == "nt":
            import string

            drives = []
            for letter in string.ascii_uppercase:
                path = f"{letter}:\\"
                if os.path.exists(path):
                    drives.append(path)
            return drives or [os.path.abspath(os.sep)]
        return ["/"]

    def _detect_gpus(self) -> list[GpuInfo]:
        if self._gpu_provider is not None:
            try:
                return list(self._gpu_provider())
            except Exception:  # noqa: BLE001
                return []
        return []

    @staticmethod
    def _psutil() -> Any | None:
        try:
            import psutil  # type: ignore[import-not-found]
        except ImportError:
            return None
        return psutil
