"""Cloud agent: useful work while the PC is away (Gate 28).

The cloud agent is a strict subset machine: it accepts ONLY tasks whose
inputs are cloud-safe (SYNC_ALLOWED metadata, no LOCAL_ONLY data), runs
plans whose tools are all in its explicit allow-list through the normal
ToolRunner + policy path, and hands results back through SyncManager.
LOCAL_ONLY data stays unreachable by construction: it is never synced,
so it simply is not there. Resume on the PC reuses versions/conflicts.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ai_ecosystem.agent.executor.executor import ExecutionResult, OverallStatus
from ai_ecosystem.cloud.sync import SyncClass, SyncManager, SyncObject
from ai_ecosystem.core.errors.exceptions import DomainValidationError
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.domain import Plan
from ai_ecosystem.core.models.enums import EventType


class CloudUnsafeError(DomainValidationError):
    """A task was refused by the cloud agent (local-only or unscoped)."""


class CloudAgent:
    """Executes cloud-safe plans; rejects everything else explicitly."""

    def __init__(
        self,
        executor_factory: Callable[[], Any],
        sync: SyncManager | None = None,
        allowed_tools: list[str] | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._executor_factory = executor_factory
        self._sync = sync
        self._allowed_tools = set(allowed_tools or [])
        self._bus = bus

    def is_cloud_safe(self, plan: Plan, inputs: list[SyncObject]) -> tuple[bool, str]:
        """Gate check: tools allow-listed AND inputs all SYNC_ALLOWED."""
        for step in plan.steps:
            for tool in step.tools:
                if tool not in self._allowed_tools:
                    return False, f"tool {tool!r} is not cloud-runnable"
        policy = self._sync.policy if self._sync is not None else None
        for obj in inputs:
            sync_class = (
                policy.classify(obj) if policy is not None else SyncClass.LOCAL_ONLY
            )
            if sync_class is not SyncClass.SYNC_ALLOWED:
                return False, f"object {obj.object_id!r} is {sync_class.value}"
        return True, "cloud-safe"

    def accept(self, task_id: str, plan: Plan, inputs: list[SyncObject]) -> None:
        """Admit a task or raise CloudUnsafeError (never partial)."""
        safe, reason = self.is_cloud_safe(plan, inputs)
        if not safe:
            raise CloudUnsafeError(f"task {task_id!r} refused: {reason}")
        self._emit(EventType.AGENT_STARTED, task_id, {"agent": "cloud"})

    def run_cloud_task(
        self, task_id: str, plan: Plan, arguments: dict | None = None
    ) -> ExecutionResult:
        """Execute an admitted plan through the normal executor path."""
        executor = self._executor_factory()
        result = executor.execute(task_id, plan, arguments=arguments or {})
        self._emit(
            EventType.AGENT_COMPLETED,
            task_id,
            {"agent": "cloud", "success": result.status is OverallStatus.COMPLETED},
        )
        return result

    def publish_results(
        self, task_id: str, result: ExecutionResult, objects: list[SyncObject]
    ) -> Any:
        """Sync result metadata back (uses the normal sync machinery).

        Forbidden objects are refused up front instead of relying on
        the sync pass to skip them: publishing must be explicit.
        """
        if self._sync is None:
            raise CloudUnsafeError("cloud agent has no sync manager")
        for obj in objects:
            if self._sync.policy.classify(obj) is SyncClass.SYNC_FORBIDDEN:
                raise CloudUnsafeError(f"object {obj.object_id!r} is forbidden to sync")
        return self._sync.sync(objects)

    def resume_handoff(
        self,
        task_id: str,
        local_version: str,
        remote_version: str,
        local_hash: str = "",
        remote_hash: str = "",
    ) -> str:
        """Compare versions AND hashes for PC resume.

        Equal versions with differing known hashes still conflict:
        version equality alone cannot prove identical content.
        """
        if local_version != remote_version:
            return "conflict"
        if local_hash and remote_hash and local_hash != remote_hash:
            return "conflict"
        return "resume"

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(event_type=event_type, task_id=task_id, payload=payload)
            )


def cloud_safe_plan(plan: Plan, allowed_tools: set[str]) -> tuple[bool, str]:
    """Standalone admissibility check (planners/routers use this first)."""
    for step in plan.steps:
        for tool in step.tools:
            if tool not in allowed_tools:
                return False, f"tool {tool!r} is not cloud-runnable"
    return True, "cloud-safe"
