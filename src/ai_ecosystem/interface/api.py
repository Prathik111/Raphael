"""Local runtime API: the ONLY desktop-to-runtime boundary (Gate 20).

The UI is a client: it submits goals, reads status/events, and cancels
tasks. It cannot execute tools, mutate the database, decide
permissions, or see credentials -- those stay behind this facade in
the Python runtime. All responses are JSON-serializable dicts.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ai_ecosystem.core.errors.exceptions import (
    AiEcosystemError,
    ResourceNotFoundError,
)
from ai_ecosystem.core.events.bus import Event
from ai_ecosystem.core.models.domain import Task
from ai_ecosystem.core.models.enums import TaskState
from ai_ecosystem.core.runtime import state_machine as sm
from ai_ecosystem.core.runtime.task_manager import AgentRuntime

MAX_GOAL_CHARS = 4_000


class ApiError(AiEcosystemError):
    """Structured API failure (malformed request, unknown task, ...)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class RuntimeAPI:
    """In-process facade over the agent runtime for UI clients."""

    def __init__(
        self,
        runtime: AgentRuntime,
        dispatch: Callable[[str], None] | None = None,
        awareness: Any | None = None,
        models: Any | None = None,
        skills: Any | None = None,
        event_store: Any | None = None,
        on_cancel: Callable[[str], None] | None = None,
    ) -> None:
        self._runtime = runtime
        self._dispatch = dispatch
        self._awareness = awareness
        self._models = models
        self._skills = skills
        self._event_store = event_store
        self._on_cancel = on_cancel

    # -- tasks -----------------------------------------------------------

    def create_task(self, goal: Any) -> dict[str, Any]:
        """Submit a goal; returns the new task's identity and status."""
        if not isinstance(goal, str) or not goal.strip():
            raise ApiError("malformed_request", "goal must be a non-empty string")
        if len(goal) > MAX_GOAL_CHARS:
            raise ApiError("malformed_request", "goal exceeds size limit")
        task, _ = self._runtime.manager.create_task(goal.strip(), goal.strip())
        if self._dispatch is not None:
            self._dispatch(task.id)
        return self._task_view(task)

    def get_task(self, task_id: str) -> dict[str, Any]:
        """One task's identity and status (never fabricated)."""
        return self._task_view(self._require(task_id))

    def list_tasks(self) -> list[dict[str, Any]]:
        """All known tasks, newest last."""
        tasks = sorted(self._runtime.manager._tasks.list(), key=lambda t: t.created_at)
        return [self._task_view(task) for task in tasks]

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        """Cancel a task (safe: a legal state transition, nothing killed)."""
        task = self._require(task_id)
        if task.state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
            raise ApiError("invalid_transition", f"task is already {task.state.value}")
        updated = self._runtime.manager.transition(task.id, TaskState.CANCELLED)
        if self._on_cancel is not None:
            try:
                self._on_cancel(task.id)
            except Exception:  # noqa: BLE001 -- state already terminal; hook is best effort
                pass
        return self._task_view(updated)

    def pause_task(self, task_id: str) -> dict[str, Any]:
        """Pause is not supported by the runtime: explicit, not silent."""
        self._require(task_id)
        raise ApiError("unsupported", "pause/resume is not implemented")

    def resume_task(self, task_id: str) -> dict[str, Any]:
        """Resume is not supported by the runtime: explicit, not silent."""
        self._require(task_id)
        raise ApiError("unsupported", "pause/resume is not implemented")

    def get_task_status(self, task_id: str) -> dict[str, Any]:
        """Current lifecycle state of one task."""
        task = self._require(task_id)
        return {
            "task_id": task.id,
            "state": task.state.value,
            "next": sorted(s.value for s in sm.next_states(task.state)),
        }

    def get_task_events(self, task_id: str, since: int = 0) -> list[dict[str, Any]]:
        """Event Store entries for one task (polling fallback for the UI).

        ``since`` is a cursor: the last sequence the caller has seen
        (0 = nothing seen yet). Sequences are the store's durable
        append numbers, so polling never duplicates or misses entries.
        """
        self._require(task_id)
        return [
            {
                "seq": seq,
                "type": event.event_type.value,
                "task_id": event.task_id,
                "payload": _redacted(event),
                "created_at": event.created_at.isoformat(),
            }
            for seq, event in self._events_for_since(task_id, since)
        ]

    def get_task_result(self, task_id: str) -> dict[str, Any]:
        """The agent's persisted result (summary, steps, verification).

        Raises ApiError("unavailable") while the task is still running:
        the UI polls this after the task reaches a terminal state.
        """
        self._require(task_id)
        ctx = self._runtime.manager.get_context(task_id)
        result = None
        if ctx is not None:
            result = (ctx.metadata or {}).get("agent_result")
        if not isinstance(result, dict):
            raise ApiError("unavailable", "task has no result yet")
        return result

    # -- runtime state ----------------------------------------------------

    def get_agent_status(self) -> dict[str, Any]:
        """Counts by lifecycle state (no internals, no secrets)."""
        counts: dict[str, int] = {}
        for task in self._runtime.manager._tasks.list():
            counts[task.state.value] = counts.get(task.state.value, 0) + 1
        return {"tasks": counts}

    def recent_permission_events(self, limit: int = 20) -> list[dict[str, Any]]:
        """Latest permission decisions across tasks (phone approval view)."""
        if self._event_store is None:
            return []
        kinds = {"PermissionGranted", "PermissionDenied"}
        matched = [
            event
            for event in self._event_store.list()
            if event.event_type.value in kinds
        ]
        return [
            {
                "type": event.event_type.value,
                "task_id": event.task_id,
                "payload": _redacted(event),
                "created_at": event.created_at.isoformat(),
            }
            for event in matched[-max(0, limit) :]
        ]

    def get_system_awareness(self) -> dict[str, Any]:
        """Redacted awareness summary (unavailable when not configured)."""
        if self._awareness is None:
            raise ApiError("unavailable", "system awareness not configured")
        snapshot = self._awareness.snapshot()
        return {
            "os": snapshot.operating_system,
            "arch": snapshot.architecture,
            "pressure": snapshot.pressure.value,
            "capabilities": {
                n: v for n, v in snapshot.capabilities.model_dump().items() if v
            },
        }

    def get_models(self) -> list[dict[str, Any]]:
        """Provider ids + capabilities only (never credentials)."""
        if self._models is None:
            return []
        return [
            {"provider_id": p.provider_id, "capabilities": p.capabilities.model_dump()}
            for p in self._models
        ]

    def get_skills(self) -> list[dict[str, Any]]:
        """Skill names + versions only (templates stay runtime-side)."""
        if self._skills is None:
            return []
        return [
            {"name": s.name, "version": s.version, "status": s.status.value}
            for s in self._skills
        ]

    # -- internals ----------------------------------------------------------

    def _require(self, task_id: str) -> Task:
        if not isinstance(task_id, str) or not task_id:
            raise ApiError("malformed_request", "task_id must be a non-empty string")
        task = self._runtime.manager._tasks.get(task_id)
        if task is None:
            raise ResourceNotFoundError("Task", task_id)
        return task

    @staticmethod
    def _task_view(task: Task) -> dict[str, Any]:
        return {
            "task_id": task.id,
            "title": task.title,
            "state": task.state.value,
            "created_at": task.created_at.isoformat(),
            "completed": task.state
            in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED),
        }

    def _events_for(self, task_id: str) -> list[Event]:
        if self._event_store is None:
            return []
        return [e for e in self._event_store.list() if e.task_id == task_id]

    def _events_for_since(self, task_id: str, since: int) -> list[tuple[int, Event]]:
        """(durable sequence, event) for one task newer than ``since``."""
        if self._event_store is None:
            return []
        events_since = getattr(self._event_store, "events_since", None)
        if callable(events_since):
            return [
                (seq, event)
                for seq, event in events_since(since - 1)
                if event.task_id == task_id
            ]
        return [
            (seq, event)
            for seq, event in enumerate(self._event_store.list())
            if event.task_id == task_id and seq >= since
        ]


def _redacted(event: Event) -> dict[str, Any]:
    """Event payloads pass through the CENTRAL redactor.

    Review fix 08/25: one redaction service everywhere. secrets.redact
    masks secret-smelling keys AND known credential formats at any
    depth -- strictly stronger than the old substring list, and the
    same function the runner, logs, and audit path use.
    """
    from ai_ecosystem.core.secrets import redact

    try:
        return redact(dict(event.payload))
    except Exception:  # noqa: BLE001 -- redaction degrades, never fails
        return {"redacted": True}
