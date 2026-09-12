"""Process-isolated sandbox provider with fail-closed resource/network policy."""

from __future__ import annotations

import ctypes
import multiprocessing as mp
import os
import queue
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

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


def _scrubbed_env(extra: dict[str, str] | None = None) -> dict[str, str]:
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
    """Terminate the worker and its descendants."""
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


# Windows Job Object ctypes structures must be module-level because nested
# class bodies cannot resolve sibling nested classes by lexical scope.
class _BasicLimit(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _WindowsJob:
    """Windows Job Object enforcing descendant lifetime and resource limits."""

    JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JobObjectExtendedLimitInformation = 9
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_TERMINATE = 0x0001
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimit),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    def __init__(self, profile: SandboxProfile, has_subprocess_capability: bool) -> None:
        self.handle = None
        if os.name != "nt":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32 = kernel32
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        kernel32.SetInformationJobObject.restype = ctypes.c_int
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.TerminateJobObject.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        self.handle = handle
        limits = self._ExtendedLimit()
        flags = self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if profile.max_processes > 0:
            flags |= self.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            limits.BasicLimitInformation.ActiveProcessLimit = profile.max_processes + (
                1 if has_subprocess_capability else 0
            )
        if profile.max_memory_mb > 0:
            flags |= self.JOB_OBJECT_LIMIT_PROCESS_MEMORY
            limits.ProcessMemoryLimit = profile.max_memory_mb * 1024 * 1024
        if profile.max_cpu_s > 0:
            flags |= self.JOB_OBJECT_LIMIT_JOB_TIME
            limits.BasicLimitInformation.PerJobUserTimeLimit = int(profile.max_cpu_s * 10_000_000)
        limits.BasicLimitInformation.LimitFlags = flags
        ok = kernel32.SetInformationJobObject(
            handle,
            self.JobObjectExtendedLimitInformation,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        if not ok:
            error = ctypes.get_last_error()
            self.close()
            raise OSError(error, "SetInformationJobObject failed")

    def assign(self, process: mp.Process) -> None:
        if self.handle is None or process.pid is None:
            return
        access = (
            self.PROCESS_SET_QUOTA | self.PROCESS_TERMINATE | self.PROCESS_QUERY_LIMITED_INFORMATION
        )
        process_handle = self._kernel32.OpenProcess(access, 0, process.pid)
        if not process_handle:
            error = ctypes.get_last_error()
            self.close()
            raise OSError(error, "OpenProcess failed")
        try:
            ok = self._kernel32.AssignProcessToJobObject(self.handle, process_handle)
            if not ok:
                error = ctypes.get_last_error()
                self.close()
                raise OSError(error, "AssignProcessToJobObject failed")
        finally:
            self._kernel32.CloseHandle(process_handle)

    def terminate(self) -> None:
        if self.handle:
            self._kernel32.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if self.handle:
            self._kernel32.CloseHandle(self.handle)
            self.handle = None


class LocalSandboxProvider(SandboxProvider):
    """Run sandboxed handlers in killable worker processes."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.RLock] = {}
        self._meta_lock = threading.Lock()

    def _lock_for(self, profile: str) -> threading.RLock:
        with self._meta_lock:
            return self._locks.setdefault(profile, threading.RLock())

    def scrubbed_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
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
        if profile.max_processes < 1:
            raise ToolExecutionError(tool.name, "sandbox max_processes must be at least 1")
        if profile.max_disk_mb:
            raise ToolExecutionError(
                tool.name,
                "requested disk quota is not enforceable by the local sandbox provider",
            )
        if os.name != "nt" and (profile.max_memory_mb or profile.max_cpu_s):
            raise ToolExecutionError(
                tool.name,
                "requested memory/CPU quota is only enforced by the Windows Job Object provider",
            )
        if tool.network_access and not profile.allow_network:
            raise ToolExecutionError(
                tool.name, f"network use denied by sandbox profile {profile.name!r}"
            )
        if tool.secrets_access:
            raise ToolExecutionError(
                tool.name, "secret access is not granted by the local sandbox profile"
            )
        has_subprocess_capability = "subprocess" in set(tool.capabilities)
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
                    has_subprocess_capability,
                    clean_env,
                    result_queue,
                ),
            )
            job = _WindowsJob(profile, has_subprocess_capability)
            try:
                os.environ.clear()
                os.environ.update(clean_env)
                try:
                    process.start()
                except Exception:
                    job.close()
                    raise
                if os.name == "nt":
                    try:
                        job.assign(process)
                    except OSError as exc:
                        _terminate_process(process)
                        raise ToolExecutionError(
                            tool.name, f"could not attach worker to Windows Job Object: {exc}"
                        ) from exc
            finally:
                os.environ.clear()
                os.environ.update(original_env)

            try:
                started = time.monotonic()
                while process.is_alive():
                    if cancel_token is not None and getattr(cancel_token, "cancelled", False):
                        if os.name == "nt":
                            job.terminate()
                        _terminate_process(process)
                        raise ToolExecutionError(tool.name, "execution cancelled")
                    if time.monotonic() - started >= max(deadline, 0.0):
                        if os.name == "nt":
                            job.terminate()
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
                if os.name == "nt":
                    job.close()
                if process.is_alive():
                    _terminate_process(process)
                result_queue.close()
                result_queue.join_thread()
