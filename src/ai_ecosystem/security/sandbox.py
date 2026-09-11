"""Process-isolated sandbox provider with fail-closed network policy."""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, Optional

from pydantic import BaseModel

from ai_ecosystem.core.errors.exceptions import (
    DomainValidationError,
    ToolExecutionError,
    ToolTimeoutError,
)
from ai_ecosystem.core.models.domain import Tool, ToolResult
from ai_ecosystem.core.secrets import looks_secret

ToolHandler = Callable[[dict], ToolResult]


class SandboxProfile(BaseModel):
    name: str = "default"
    fs_root: str = ""
    allow_network: bool = False
    timeout_s: float = 60.0
    max_memory_mb: int = 0
    max_disk_mb: int = 0
    max_cpu_s: float = 0.0
    max_processes: int = 1


class SandboxProvider(ABC):
    @abstractmethod
    def run(
        self,
        tool: Tool,
        handler: ToolHandler,
        arguments: dict,
        profile: SandboxProfile,
        timeout_s: float,
        cancel_token: Any = None,
    ) -> ToolResult:
        raise NotImplementedError


class SandboxUnavailableError(ToolExecutionError):
    def __init__(self, tool: str) -> None:
        super().__init__(tool, "sandbox required but no provider configured")


def _scrubbed_env(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    clean = {key: value for key, value in os.environ.items() if not looks_secret(key)}
    clean.update(extra or {})
    return clean


def _worker(
    handler: ToolHandler,
    arguments: dict,
    profile: SandboxProfile,
    has_subprocess_capability: bool,
    clean_env: dict[str, str],
    result_queue: Any,
) -> None:
    """Worker entrypoint; must stay top-level for Windows spawn."""
    try:
        os.environ.clear()
        os.environ.update(clean_env)
        if profile.fs_root and has_subprocess_capability:
            os.chdir(profile.fs_root)
        result_queue.put((True, handler(arguments)))
    except BaseException as exc:  # noqa: BLE001 - cross-process transport
        result_queue.put((False, f"{type(exc).__name__}: {exc}"))


def _terminate_process(process: mp.Process) -> None:
    """Terminate the worker; POSIX workers also own a process group."""
    if not process.is_alive():
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, 9)
        except (ProcessLookupError, PermissionError, OSError):
            process.kill()
    else:
        process.kill()
    process.join(timeout=1.0)


class LocalSandboxProvider(SandboxProvider):
    """Run sandboxed handlers in killable worker processes."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.RLock] = {}
        self._meta_lock = threading.Lock()

    def _lock_for(self, profile: str) -> threading.RLock:
        with self._meta_lock:
            return self._locks.setdefault(profile, threading.RLock())

    def scrubbed_env(self, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
        return _scrubbed_env(extra)

    def run(
        self,
        tool: Tool,
        handler: ToolHandler,
        arguments: dict,
        profile: SandboxProfile,
        timeout_s: float,
        cancel_token: Any = None,
    ) -> ToolResult:
        if profile.max_processes != 1:
            raise ToolExecutionError(
                tool.name, "local sandbox currently supports max_processes=1 only"
            )
        if profile.max_memory_mb or profile.max_cpu_s or profile.max_disk_mb:
            raise ToolExecutionError(
                tool.name,
                "requested resource quota is not enforceable by the local sandbox provider",
            )
        if tool.network_access and not profile.allow_network:
            raise ToolExecutionError(
                tool.name, f"network use denied by sandbox profile {profile.name!r}"
            )
        if tool.secrets_access:
            raise ToolExecutionError(
                tool.name, "secret access is not granted by the local sandbox profile"
            )
        deadline = min(timeout_s, profile.timeout_s) if profile.timeout_s > 0 else timeout_s
        lock = self._lock_for(profile.name)
        with lock:
            ctx = mp.get_context("spawn") if os.name == "nt" else mp.get_context("fork")
            result_queue = ctx.Queue(maxsize=1)
            clean_env = _scrubbed_env()
            original_env = dict(os.environ)
            process = ctx.Process(
                target=_worker,
                args=(
                    handler,
                    dict(arguments),
                    profile,
                    "subprocess" in set(tool.capabilities),
                    clean_env,
                    result_queue,
                ),
            )
            try:
                os.environ.clear()
                os.environ.update(clean_env)
                process.start()
            finally:
                os.environ.clear()
                os.environ.update(original_env)

            try:
                started = time.monotonic()
                while process.is_alive():
                    if cancel_token is not None and getattr(cancel_token, "cancelled", False):
                        _terminate_process(process)
                        raise ToolExecutionError(tool.name, "execution cancelled")
                    if time.monotonic() - started >= max(deadline, 0.0):
                        _terminate_process(process)
                        raise ToolTimeoutError(tool.name, deadline)
                    time.sleep(0.02)
                process.join(timeout=0.2)
                try:
                    ok, value = result_queue.get(timeout=0.2)
                except queue.Empty as exc:
                    raise ToolExecutionError(
                        tool.name, "sandbox worker exited without a result"
                    ) from exc
                if not ok:
                    raise ToolExecutionError(tool.name, value)
                if not isinstance(value, ToolResult):
                    raise DomainValidationError("sandbox worker returned a non-ToolResult")
                return value
            finally:
                if process.is_alive():
                    _terminate_process(process)
                result_queue.close()
                result_queue.join_thread()
