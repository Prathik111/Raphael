"""Structured system-awareness models (Gate 15).

Snapshots describe the machine for planning; they never contain
credentials, document contents, keystrokes, or media captures. Host
identity is opt-in (off by default).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from ai_ecosystem.core.models.base import Entity, utcnow


class PressureLevel(str, Enum):
    """Coarse resource-pressure classification."""

    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class CpuInfo(BaseModel):
    """Processor description and load (percentages may be unknown)."""

    model: str = ""
    logical_processors: int = 0
    utilization_percent: float | None = None
    load_average: list[float] = Field(default_factory=list)


class MemoryInfo(BaseModel):
    """RAM totals in bytes (percentages may be unknown)."""

    total_bytes: int = 0
    available_bytes: int = 0
    used_bytes: int = 0
    utilization_percent: float | None = None


class GpuInfo(BaseModel):
    """One GPU device (empty list when none detected)."""

    name: str = ""
    vram_total_bytes: int = 0
    vram_available_bytes: int = 0
    utilization_percent: float | None = None


class StorageVolume(BaseModel):
    """One mounted volume (no file scanning involved)."""

    mount: str = ""
    total_bytes: int = 0
    available_bytes: int = 0
    utilization_percent: float | None = None


class ProcessInfo(BaseModel):
    """One active process (name + coarse share, never arguments)."""

    name: str = ""
    cpu_percent: float | None = None
    memory_bytes: int = 0


class Capabilities(BaseModel):
    """Structural capability flags (never model-generated text)."""

    local_model_available: bool = False
    gpu_available: bool = False
    cuda_available: bool = False
    network_available: bool = False
    git_available: bool = False
    python_available: bool = False
    docker_available: bool = False
    storage_available: bool = False
    cloud_connector_available: bool = False


class SystemSnapshot(Entity):
    """Point-in-time machine description for planning."""

    operating_system: str = ""
    architecture: str = ""
    hostname: str = ""
    cpu: CpuInfo = Field(default_factory=CpuInfo)
    memory: MemoryInfo = Field(default_factory=MemoryInfo)
    gpus: list[GpuInfo] = Field(default_factory=list)
    storage: list[StorageVolume] = Field(default_factory=list)
    processes: list[ProcessInfo] = Field(default_factory=list)
    capabilities: Capabilities = Field(default_factory=Capabilities)
    pressure: PressureLevel = PressureLevel.UNKNOWN
    collected_at: datetime = Field(default_factory=utcnow)


class AwarenessContext(BaseModel):
    """Planner-facing answers: what can this machine do right now?"""

    can_compute_locally: bool = False
    available_capabilities: list[str] = Field(default_factory=list)
    unavailable_capabilities: list[str] = Field(default_factory=list)
    constrained_resources: list[str] = Field(default_factory=list)
    pressure: PressureLevel = PressureLevel.UNKNOWN
    summary: str = ""
