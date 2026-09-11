"""Live visualization adapter: events in, UI view-models out (Gate 21).

This is presentation, never a source of truth: every field derives
from runtime events (or an explicit note_* call for data the runtime
does not emit, like the active model). The adapter is idempotent --
re-ingesting the same stream changes nothing -- so reconnects simply
replay. Sensitive arguments/file contents are never stored: tool
activity keeps names, states, and durations only.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.enums import EventType


class StepView(BaseModel):
    """One DAG node's display state."""

    step_id: str = ""
    label: str = ""
    state: str = "PENDING"
    dependencies: list[str] = Field(default_factory=list)


class ToolActivity(BaseModel):
    """One tool call's display summary (no arguments, no outputs)."""

    call_id: str = ""
    tool: str = ""
    state: str = "REQUESTED"
    duration_ms: float | None = None


class PermissionView(BaseModel):
    """One authorization decision, or a request still awaiting policy."""

    call_id: str = ""
    tool: str = ""
    risk: str = ""
    reason: str = ""
    decided: bool = False
    granted: bool = False


class AgentView(BaseModel):
    """One agent's display state."""

    agent_id: str = ""
    status: str = "READY"
    task_id: str = ""
    current_step: str = ""


class TaskView(BaseModel):
    """Everything the UI shows for one task."""

    task_id: str = ""
    state: str = "CREATED"
    steps: dict[str, StepView] = Field(default_factory=dict)
    agents: dict[str, AgentView] = Field(default_factory=dict)
    tools: dict[str, ToolActivity] = Field(default_factory=dict)
    permissions: dict[str, PermissionView] = Field(default_factory=dict)
    verification: str = "PENDING"
    recovery: str = ""
    model: str = ""
    provider: str = ""
    last_seq: int = -1


class _ToolClock:
    def __init__(self) -> None:
        self._started: dict[str, Any] = {}

    def start(self, call_id: str, at: Any) -> None:
        self._started.setdefault(call_id, at)

    def stop(self, call_id: str, at: Any) -> float | None:
        started = self._started.pop(call_id, None)
        if started is None or at is None:
            return None
        try:
            return max(0.0, (at - started).total_seconds() * 1000.0)
        except TypeError:
            return None


class EventAdapter:
    """Folds the runtime event stream into per-task view models."""

    def __init__(self, bus: EventBus | None = None) -> None:
        self._tasks: dict[str, TaskView] = {}
        self._clocks: dict[str, _ToolClock] = {}
        if bus is not None:
            bus.subscribe_all(self.ingest)

    def ingest(self, event: Event) -> None:
        """Fold one event (idempotent under re-ingest by the caller)."""
        task_id = event.task_id or ""
        if not task_id:
            return
        view = self._tasks.setdefault(task_id, TaskView(task_id=task_id))
        clock = self._clocks.setdefault(task_id, _ToolClock())
        kind = event.event_type
        payload = event.payload
        if kind is EventType.TASK_CREATED:
            view.state = "CREATED"
        elif kind is EventType.PLAN_CREATED:
            for index, step in enumerate(payload.get("steps", [])):
                sid = step.get("id", f"step-{index}") if isinstance(step, dict) else str(step)
                view.steps[sid] = StepView(step_id=sid, label=sid)
        elif kind is EventType.GRAPH_STARTED:
            if view.state not in ("COMPLETED", "FAILED", "CANCELLED"):
                view.state = "EXECUTING"
        elif kind is EventType.STEP_READY:
            self._step(view, payload).state = "READY"
        elif kind is EventType.STEP_STARTED:
            node = self._step(view, payload)
            node.state = "RUNNING"
            # NOTE: step events carry no agent attribution, so the view
            # deliberately does NOT guess which agent runs the step.
        elif kind is EventType.STEP_COMPLETED:
            self._step(view, payload).state = "SUCCEEDED"
        elif kind is EventType.STEP_FAILED:
            self._step(view, payload).state = "FAILED"
            view.state = "FAILED"
        elif kind is EventType.STEP_TIMED_OUT:
            self._step(view, payload).state = "TIMED_OUT"
            view.state = "FAILED"
        elif kind is EventType.STEP_CANCELLED:
            self._step(view, payload).state = "CANCELLED"
        elif kind is EventType.STEP_SKIPPED:
            self._step(view, payload).state = "SKIPPED"
        elif kind is EventType.GRAPH_COMPLETED:
            if view.state not in ("FAILED", "CANCELLED", "RECOVERING"):
                view.state = "COMPLETED"
        elif kind is EventType.TOOL_REQUESTED:
            call_id = str(payload.get("call_id", ""))
            if call_id not in view.tools:
                view.tools[call_id] = ToolActivity(
                    call_id=call_id, tool=str(payload.get("tool", ""))
                )
            self._pending_permission(view, payload)
        elif kind is EventType.TOOL_STARTED:
            activity = self._tool(view, payload)
            activity.state = "RUNNING"
            clock.start(activity.call_id, event.created_at)
        elif kind is EventType.TOOL_COMPLETED:
            activity = self._tool(view, payload)
            activity.state = "COMPLETED"
            activity.duration_ms = clock.stop(activity.call_id, event.created_at)
            self._resolve_permission(view, payload, True)
        elif kind is EventType.TOOL_FAILED:
            activity = self._tool(view, payload)
            activity.state = "FAILED"
            activity.duration_ms = clock.stop(activity.call_id, event.created_at)
            self._resolve_permission(view, payload, True)
        elif kind is EventType.PERMISSION_DENIED:
            call_id = str(payload.get("call_id", ""))
            entry = view.permissions.get(call_id)
            if entry is None:
                entry = PermissionView(call_id=call_id, tool=str(payload.get("tool", "")))
                view.permissions[call_id] = entry
            entry.decided = True
            entry.granted = False
            entry.reason = str(payload.get("reason", ""))
            if call_id in view.tools:
                view.tools[call_id].state = "DENIED"
        elif kind is EventType.PERMISSION_GRANTED:
            entry = self._pending_permission(view, payload)
            entry.decided = True
            entry.granted = True
        elif kind is EventType.VERIFICATION_STARTED:
            view.verification = "RUNNING"
        elif kind is EventType.VERIFICATION_PASSED:
            view.verification = "PASSED"
        elif kind is EventType.VERIFICATION_FAILED:
            view.verification = "FAILED"
        elif kind is EventType.VERIFICATION_INCONCLUSIVE:
            view.verification = "INCONCLUSIVE"
        elif kind is EventType.VERIFICATION_ERROR:
            view.verification = "ERROR"
        elif kind is EventType.RECOVERY_STARTED:
            view.recovery = "RUNNING"
            view.state = "RECOVERING"
        elif kind is EventType.RECOVERY_DECIDED:
            view.recovery = str(payload.get("action", "DECIDED"))
        elif kind is EventType.RECOVERY_EXHAUSTED:
            view.recovery = "EXHAUSTED"
        elif kind is EventType.AGENT_STARTED:
            agent_id = str(payload.get("agent_id", ""))
            if agent_id:
                agent = view.agents.setdefault(agent_id, AgentView(agent_id=agent_id))
                agent.status = "RUNNING"
                agent.task_id = task_id
        elif kind is EventType.AGENT_COMPLETED:
            agent_id = str(payload.get("agent_id", ""))
            if agent_id and agent_id in view.agents:
                view.agents[agent_id].status = "COMPLETED" if payload.get("success") else "FAILED"

    def ingest_store(self, events: list[Event], last_seen: int = -1) -> int:
        """Polling fallback: fold events newer than last_seen; returns new mark."""
        mark = last_seen
        for seq, event in enumerate(events):
            if seq <= last_seen:
                continue  # stale or duplicate: skip honestly
            self.ingest(event)
            mark = seq
        return mark

    def reset(self, task_id: str = "") -> None:
        """Forget derived state (reconnect path replays afterwards)."""
        if task_id:
            self._tasks.pop(task_id, None)
            self._clocks.pop(task_id, None)
        else:
            self._tasks.clear()
            self._clocks.clear()

    def snapshot(self, task_id: str) -> TaskView | None:
        """Current view for one task (None when never observed)."""
        return self._tasks.get(task_id)

    def dag(self, task_id: str) -> dict[str, Any]:
        """DAG rendering data generated from observed step state."""
        view = self._tasks.get(task_id)
        if view is None:
            return {"nodes": [], "edges": []}
        nodes = [
            {"id": node.step_id, "label": node.label or node.step_id, "state": node.state}
            for node in view.steps.values()
        ]
        known = set(view.steps)
        edges = [
            {"from": dep, "to": node.step_id}
            for node in view.steps.values()
            for dep in node.dependencies
            if dep in known
        ]
        return {"nodes": nodes, "edges": edges}

    def note_model(self, task_id: str, model: str, provider: str = "") -> None:
        """Record which model served a task (runtime emits no such event)."""
        view = self._tasks.setdefault(task_id, TaskView(task_id=task_id))
        view.model = model
        view.provider = provider

    def note_step_deps(
        self, task_id: str, step_id: str, dependencies: list[str], label: str = ""
    ) -> None:
        """Attach plan structure the event stream does not carry."""
        node = self._step_by_id(task_id, step_id)
        node.dependencies = list(dependencies)
        if label:
            node.label = label

    @staticmethod
    def _step(view: TaskView, payload: dict) -> StepView:
        step_id = str(payload.get("step_id", ""))
        return view.steps.setdefault(step_id, StepView(step_id=step_id))

    def _step_by_id(self, task_id: str, step_id: str) -> StepView:
        view = self._tasks.setdefault(task_id, TaskView(task_id=task_id))
        return view.steps.setdefault(step_id, StepView(step_id=step_id))

    @staticmethod
    def _tool(view: TaskView, payload: dict) -> ToolActivity:
        call_id = str(payload.get("call_id", ""))
        return view.tools.setdefault(call_id, ToolActivity(call_id=call_id))

    @staticmethod
    def _pending_permission(view: TaskView, payload: dict) -> PermissionView:
        call_id = str(payload.get("call_id", ""))
        entry = view.permissions.get(call_id)
        if entry is None:
            entry = PermissionView(call_id=call_id, tool=str(payload.get("tool", "")))
            view.permissions[call_id] = entry
        return entry

    @staticmethod
    def _resolve_permission(view: TaskView, payload: dict, granted: bool) -> None:
        call_id = str(payload.get("call_id", ""))
        entry = view.permissions.get(call_id)
        if entry is not None and not entry.decided:
            entry.decided = True
            entry.granted = granted
