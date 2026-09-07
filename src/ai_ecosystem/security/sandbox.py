"""Sandboxing: isolation where the platform permits it (Gate 30).

Honest scope for userspace Python on a general OS:

ENFORCED: per-call timeouts, cwd confinement for subprocess tools,
secret-scrubbed environment under a process-wide lock, sequential
execution within one provider, network-category denial.
ADVISORY (recorded, not enforced): memory/disk/CPU/process caps --
true enforcement needs OS primitives (job objects, cgroups, containers)
owned by a later platform gate.

Sandboxing never replaces authorization: sandboxed calls still pass
the full policy path first, and tools flagged requires_sandbox fail
closed when no provider is configured.

Caveat: environment scrubbing is process-wide for the call duration
(other threads briefly see the scrubbed view). Callers that need the
full environment must run inside the sandbox call or accept this.
"""

from __future__ import annotations

import os
import threading
from abc import ABC, abstractmethod
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from ai_ecosystem.core.errors.exceptions import (
    DomainValidationError,
    ToolExecutionError,
    ToolTimeoutError,
)
from ai_ecosystem.core.models.domain import Tool, ToolResult
from ai_ecosystem.core.secrets import looks_secret

ToolHandler = Callable[[dict], ToolResult]


class SandboxProfile(BaseModel):
    """Isolation contract for one class of tool executions."""

    name: str = "default"
    fs_root: str = ""
    allow_network: bool = False
    timeout_s: float = 60.0
    max_memory_mb: int = 0  # advisory: needs OS primitives
    max_disk_mb: int = 0  # advisory: needs OS primitives
    max_cpu_s: float = 0.0  # advisory: needs OS primitives
    max_processes: int = 1  # enforced: provider serializes per profile


class SandboxProvider(ABC):
    """Runs a handler under a profile's enforced guarantees."""

    @abstractmethod
    def run(self, tool: Tool, handler: ToolHandler, arguments: dict,
            profile: SandboxProfile, timeout_s: float) -> ToolResult:
        """Execute with isolation; raise ToolError subclasses on failure."""
        raise NotImplementedError


class SandboxUnavailableError(ToolExecutionError):
    """A sandboxed tool was invoked with no provider configured."""

    def __init__(self, tool: str) -> None:
        super().__init__(tool, "sandbox required but no provider configured")


def _run_daemon(handler: ToolHandler, arguments: dict,
                deadline: float, tool_name: str) -> ToolResult:
    """Run a handler on a daemon thread with a hard deadline.

    Daemon workers cannot hang pool shutdown or interpreter exit;
    late results are discarded by the caller treating timeout first.
    """
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = handler(arguments)
        except BaseException as exc:  # noqa: BLE001 -- re-raised below
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True,
                              name=f"sandbox-{tool_name}")
    thread.start()
    thread.join(max(float(deadline), 0.0))
    if thread.is_alive():
        raise FuturesTimeoutError(f"sandboxed handler exceeded {deadline}s")
    if "error" in box:
        raise box["error"]
    return box.get("result")


class LocalSandboxProvider(SandboxProvider):
    """Process-wide serialized execution with scrubbed environment.

    One lock per profile name: dangerous work never runs concurrently
    with itself, and secret-bearing environment variables are removed
    for the duration of the call (restored afterwards, even on crash).
    """

    def __init__(self) -> None:
        self._locks: dict[str, threading.RLock] = {}
        self._meta_lock = threading.Lock()

    def _lock_for(self, profile: str) -> threading.RLock:
        with self._meta_lock:
            return self._locks.setdefault(profile, threading.RLock())

    def scrubbed_env(self, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
        """Environment copy minus secret-bearing variables."""
        clean = {key: value for key, value in os.environ.items()
                 if not looks_secret(key)}
        clean.update(extra or {})
        return clean

    def run(self, tool: Tool, handler: ToolHandler, arguments: dict,
            profile: SandboxProfile, timeout_s: float) -> ToolResult:
        """Enforce network denial, cwd, env scrub, timeout, serialization."""
        if "network" in set(tool.capabilities) and not profile.allow_network:
            raise ToolExecutionError(
                tool.name, "network use denied by sandbox profile "
                           f"{profile.name!r}")
        call_args = dict(arguments)
        if profile.fs_root and "subprocess" in set(tool.capabilities):
            call_args.setdefault("cwd", profile.fs_root)
        deadline = min(timeout_s, profile.timeout_s) if profile.timeout_s > 0 \
            else timeout_s
        lock = self._lock_for(profile.name)
        with lock:
            previous = dict(os.environ)
            scrubbed = self.scrubbed_env()
            os.environ.clear()
            os.environ.update(scrubbed)
            try:
                return _run_daemon(handler, call_args, deadline, tool.name)
            except FuturesTimeoutError:
                raise ToolTimeoutError(tool.name, deadline) from None
            finally:
                os.environ.clear()
                os.environ.update(previous)
