"""Runtime DAG representation (Gate 8).

Wraps validated :class:`PlanStep` objects with execution state. Cycle and
missing-dependency detection reuse :class:`DependencyResolver`; this
module adds incremental scheduling state on top, without duplicating the
plan schema.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from ai_ecosystem.agent.planner.validator import DependencyResolver
from ai_ecosystem.core.errors.exceptions import DomainValidationError
from ai_ecosystem.core.models.base import Entity
from ai_ecosystem.core.models.domain import Plan, PlanStep, ToolResult
from ai_ecosystem.core.models.enums import StepState

TERMINAL_STEP_STATES = frozenset(
    {
        StepState.SUCCEEDED,
        StepState.FAILED,
        StepState.TIMED_OUT,
        StepState.CANCELLED,
        StepState.SKIPPED,
    }
)

_NON_SUCCESS_TERMINAL = frozenset(
    {StepState.FAILED, StepState.TIMED_OUT, StepState.CANCELLED, StepState.SKIPPED}
)

_GRAPH_TRANSITIONS: dict[StepState, frozenset[StepState]] = {
    StepState.PENDING: frozenset(
        {StepState.READY, StepState.SKIPPED, StepState.CANCELLED}
    ),
    StepState.READY: frozenset(
        {StepState.RUNNING, StepState.SKIPPED, StepState.CANCELLED}
    ),
    StepState.RUNNING: frozenset(
        {
            StepState.SUCCEEDED,
            StepState.FAILED,
            StepState.TIMED_OUT,
            StepState.CANCELLED,
        }
    ),
    StepState.SUCCEEDED: frozenset(),
    StepState.FAILED: frozenset(),
    StepState.TIMED_OUT: frozenset(),
    StepState.CANCELLED: frozenset(),
    StepState.SKIPPED: frozenset(),
}


class GraphNode(Entity):
    """One plan step plus its runtime execution state."""

    step: PlanStep = Field(default_factory=PlanStep)
    state: StepState = StepState.PENDING
    attempts: int = 0
    result: Optional[ToolResult] = None
    tool_results: list[ToolResult] = Field(default_factory=list)
    error: str = ""
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    interrupted: bool = False
    timeout_s: Optional[float] = None

    def transition(self, to_state: StepState) -> None:
        """Move state; raises DomainValidationError on illegal edges."""
        allowed = _GRAPH_TRANSITIONS.get(self.state, frozenset())
        if to_state not in allowed:
            raise DomainValidationError(
                f"illegal step transition: {self.state.value} -> {to_state.value} "
                f"(step {self.step.id!r})"
            )
        self.state = to_state
        self.touch()


class TaskGraph:
    """Deterministic, plan-ordered DAG of graph nodes."""

    def __init__(self, nodes: list[GraphNode]) -> None:
        ids = [node.step.id for node in nodes]
        if len(set(ids)) != len(ids):
            raise DomainValidationError("duplicate step ids in graph")
        known = set(ids)
        for node in nodes:
            for dep in node.step.dependencies:
                if dep not in known:
                    raise DomainValidationError(
                        f"step {node.step.id!r} depends on unknown step {dep!r}")
        self.nodes = list(nodes)
        self._by_id = {node.step.id: node for node in nodes}
        self._dependents: dict[str, list[str]] = {sid: [] for sid in ids}
        for node in nodes:
            for dep in node.step.dependencies:
                self._dependents[dep].append(node.step.id)

    @classmethod
    def from_plan(cls, plan: Plan) -> "TaskGraph":
        """Build from a plan; invalid graphs raise PlanValidationError."""
        DependencyResolver.order(plan.steps)  # cycle + missing-dep check
        return cls([GraphNode(step=step) for step in plan.steps])

    def get(self, step_id: str) -> GraphNode:
        """Node by step id (KeyError when unknown -- programming error)."""
        return self._by_id[step_id]

    def ready(self) -> list[GraphNode]:
        """PENDING nodes whose dependencies all SUCCEEDED, in plan order."""
        result = []
        for node in self.nodes:
            if node.state is not StepState.PENDING:
                continue
            if all(
                self._by_id[dep].state is StepState.SUCCEEDED
                for dep in node.step.dependencies
            ):
                result.append(node)
        return result

    def mark_unsatisfiable(self) -> list[GraphNode]:
        """Skip PENDING nodes with a terminal non-success dependency."""
        skipped = []
        for node in self.nodes:
            if node.state is not StepState.PENDING:
                continue
            bad = next(
                (
                    dep
                    for dep in node.step.dependencies
                    if self._by_id[dep].state in _NON_SUCCESS_TERMINAL
                ),
                None,
            )
            if bad is not None:
                dep_state = self._by_id[bad].state.value
                node.transition(StepState.SKIPPED)
                node.error = f"dependency {bad!r} ended {dep_state}; not executed"
                skipped.append(node)
        return skipped

    def done(self) -> bool:
        """True when every node reached a terminal state."""
        return all(node.state in TERMINAL_STEP_STATES for node in self.nodes)

    def snapshot(self) -> dict[str, Any]:
        """Serializable per-node state for persistence (restart recovery)."""
        states = {}
        for node in self.nodes:
            states[node.step.id] = {
                "state": node.state.value,
                "attempts": node.attempts,
                "error": node.error,
                "interrupted": node.interrupted,
                "timeout_s": node.timeout_s,
                "started_at": node.started_at.isoformat() if node.started_at else None,
                "completed_at": node.completed_at.isoformat()
                if node.completed_at
                else None,
                "result": node.result.model_dump() if node.result else None,
                "tool_results": [r.model_dump() for r in node.tool_results],
            }
        return {"nodes": states}

    @classmethod
    def restore(cls, plan: Plan, snapshot: dict[str, Any]) -> "TaskGraph":
        """Rebuild from a plan + snapshot.

        RUNNING nodes become PENDING with ``interrupted=True``: a dead
        process's threads do not survive, so Gate 10 must decide their
        fate -- the graph never pretends otherwise.

        Corrupt entries never abort the restore: each bad node resets
        to PENDING with an explanatory error, so crash recovery itself
        cannot crash on damaged state.
        """
        graph = cls.from_plan(plan)
        states = snapshot.get("nodes", {}) if isinstance(snapshot, dict) else {}
        if not isinstance(states, dict):
            states = {}
        for step_id, saved in states.items():
            if step_id not in graph._by_id or not isinstance(saved, dict):
                continue
            node = graph._by_id[step_id]
            try:
                state = StepState(saved.get("state", "PENDING"))
            except ValueError:
                node.error = f"unrecognized saved state {saved.get('state')!r}; reset"
                node.interrupted = True
                continue
            if state is StepState.RUNNING:
                node.interrupted = True
                node.error = "interrupted by restart; recovery pending (Gate 10)"
                continue  # stays PENDING
            try:
                node.state = state
                node.attempts = int(saved.get("attempts", 0))
                node.error = str(saved.get("error", ""))
                node.interrupted = bool(saved.get("interrupted", False))
                timeout = saved.get("timeout_s")
                node.timeout_s = float(timeout) if timeout is not None else None
                if saved.get("started_at"):
                    node.started_at = datetime.fromisoformat(saved["started_at"])
                if saved.get("completed_at"):
                    node.completed_at = datetime.fromisoformat(saved["completed_at"])
                if saved.get("result"):
                    node.result = ToolResult.model_validate(saved["result"])
                node.tool_results = [
                    ToolResult.model_validate(r)
                    for r in saved.get("tool_results", [])
                ]
            except (ValueError, TypeError) as exc:
                node.state = StepState.PENDING
                node.interrupted = True
                node.error = f"corrupt saved state discarded: {exc}"
        return graph
