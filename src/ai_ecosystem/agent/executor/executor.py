"""Deterministic parallel DAG executor with real step cancellation."""

from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from enum import Enum

from pydantic import BaseModel, Field

from ai_ecosystem.agent.executor.cancellation import CancellationToken
from ai_ecosystem.agent.executor.graph import GraphNode, TaskGraph
from ai_ecosystem.agent.planner.validator import PlanValidator
from ai_ecosystem.core.errors.exceptions import DomainValidationError, PlanValidationError
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.base import utcnow
from ai_ecosystem.core.models.domain import ExecutionContext, Plan, ToolResult
from ai_ecosystem.core.models.enums import EventType, StepState
from ai_ecosystem.core.persistence.sqlite import SqliteExecutionContextRepository
from ai_ecosystem.tools.registry.registry import ToolRegistry
from ai_ecosystem.tools.registry.runner import ToolRunner


class FailurePolicy(str, Enum):
    FAIL_FAST = "FAIL_FAST"
    CONTINUE_INDEPENDENT = "CONTINUE_INDEPENDENT"


class OverallStatus(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ExecutionResult(BaseModel):
    task_id: str = ""
    status: OverallStatus = OverallStatus.FAILED
    states: dict[str, StepState] = Field(default_factory=dict)
    succeeded: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    cancelled: list[str] = Field(default_factory=list)
    timed_out: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    results: dict[str, list[ToolResult]] = Field(default_factory=dict)
    duration_s: float = 0.0
    errors: list[str] = Field(default_factory=list)
    def all_tool_results(self) -> list[ToolResult]:
        ordered: list[ToolResult] = []
        for step_id in list(self.states): ordered.extend(self.results.get(step_id, []))
        return ordered


class ParallelExecutor:
    """Dependency-aware executor; deadlines cancel work rather than merely labelling it."""

    def __init__(self, runner: ToolRunner, registry: ToolRegistry, bus: EventBus | None = None, *,
                 max_concurrency: int = 4, failure_policy: FailurePolicy = FailurePolicy.FAIL_FAST,
                 default_step_timeout_s: float = 60.0, step_timeouts: dict[str, float] | None = None,
                 poll_interval_s: float = 0.02) -> None:
        if max_concurrency < 1: raise DomainValidationError("max_concurrency must be >= 1")
        if default_step_timeout_s <= 0: raise DomainValidationError("default_step_timeout_s must be > 0")
        self._runner = runner; self._registry = registry; self._bus = bus; self._max_concurrency = max_concurrency
        self._failure_policy = failure_policy; self._default_step_timeout_s = default_step_timeout_s
        self._step_timeouts = dict(step_timeouts or {}); self._poll_interval_s = poll_interval_s
        self._step_tokens: dict[Future, CancellationToken] = {}

    def execute(self, task_id: str, plan: Plan, *, arguments: dict[str, dict] | None = None,
                context: ExecutionContext | None = None, contexts_repo: SqliteExecutionContextRepository | None = None,
                cancel: CancellationToken | None = None) -> ExecutionResult:
        known = {tool.name for tool in self._registry.list_tools()}; validated = PlanValidator(known).validate(plan)
        return self.execute_graph(task_id, TaskGraph.from_plan(validated), arguments=arguments, context=context,
                                  contexts_repo=contexts_repo, cancel=cancel)

    def execute_graph(self, task_id: str, graph: TaskGraph, *, arguments: dict[str, dict] | None = None,
                      context: ExecutionContext | None = None, contexts_repo: SqliteExecutionContextRepository | None = None,
                      cancel: CancellationToken | None = None) -> ExecutionResult:
        known = {tool.name for tool in self._registry.list_tools()}
        for node in graph.nodes:
            for tool_name in node.step.tools:
                if tool_name not in known: raise PlanValidationError(f"step {node.step.id!r} uses unknown tool {tool_name!r}")
        args = arguments or {}; token = cancel or CancellationToken(); started = time.monotonic()
        self._emit(EventType.GRAPH_STARTED, task_id, {"steps": len(graph.nodes)}); self._persist(graph, context, contexts_repo)
        halting = False; in_flight: dict[Future, GraphNode] = {}; pool = ThreadPoolExecutor(max_workers=self._max_concurrency)
        try:
            while not graph.done():
                if token.cancelled or halting: self._stop_unstarted(graph, task_id, token, halting)
                if not token.cancelled and not halting: self._submit_ready(graph, task_id, args, pool, in_flight, token)
                if not in_flight:
                    if not self._drain_stuck(graph, task_id): break
                    self._persist(graph, context, contexts_repo); continue
                self._collect_completed(graph, task_id, in_flight, context)
                self._enforce_deadlines(graph, task_id, in_flight)
                for node in graph.mark_unsatisfiable():
                    self._emit(EventType.STEP_SKIPPED, task_id, {"step_id": node.step.id, "error": node.error})
                if self._first_failure(graph) and self._failure_policy is FailurePolicy.FAIL_FAST: halting = True
                self._persist(graph, context, contexts_repo)
        finally:
            # Cancelled/timed-out futures must never be allowed to launch more
            # tool calls. Sandboxed calls terminate their process; trusted
            # non-sandboxed handlers remain subject to their own trust boundary.
            for child in self._step_tokens.values(): child.cancel()
            pool.shutdown(wait=False, cancel_futures=True); self._step_tokens.clear()
        for node in graph.nodes:
            if node.completed_at is None: node.completed_at = utcnow()
        status = self._overall_status(graph, token); duration = time.monotonic() - started
        self._emit(EventType.GRAPH_COMPLETED, task_id, {"status": status.value, "duration_s": round(duration, 3)})
        result = self._aggregate(task_id, graph, status, duration); self._persist(graph, context, contexts_repo); return result

    def _submit_ready(self, graph: TaskGraph, task_id: str, args: dict[str, dict], pool: ThreadPoolExecutor,
                      in_flight: dict[Future, GraphNode], token: CancellationToken) -> None:
        for node in graph.ready():
            if len(in_flight) >= self._max_concurrency: break
            node.transition(StepState.READY); self._emit(EventType.STEP_READY, task_id, {"step_id": node.step.id})
            node.transition(StepState.RUNNING); node.attempts += 1; node.started_at = utcnow()
            node.timeout_s = self._step_timeouts.get(node.step.id, self._default_step_timeout_s)
            self._emit(EventType.STEP_STARTED, task_id, {"step_id": node.step.id})
            merged = {**(node.step.arguments or {}), **args.get(node.step.id, {})}
            child = token.child(node.timeout_s); future = pool.submit(self._run_node, task_id, node, merged, child)
            in_flight[future] = node; self._step_tokens[future] = child

    def _run_node(self, task_id: str, node: GraphNode, arguments: dict,
                  token: CancellationToken) -> tuple[bool, list[ToolResult], str]:
        try:
            results: list[ToolResult] = []
            for tool_name in node.step.tools:
                if token.cancelled: return False, results, "step deadline/cancellation reached before tool execution"
                call = self._registry.build_call(task_id, tool_name, arguments)
                result = self._runner.run(call, cancel_token=token); results.append(result)
                if not result.success: return False, results, result.error or f"{tool_name} failed"
            if token.cancelled: return False, results, "step deadline/cancellation reached after tool execution"
            return True, results, ""
        except Exception as exc: return False, [], f"executor error: {exc}"

    def _collect_completed(self, graph: TaskGraph, task_id: str, in_flight: dict[Future, GraphNode],
                          context: ExecutionContext | None) -> None:
        if not in_flight: return
        done, _ = wait(set(in_flight), timeout=self._poll_interval_s, return_when=FIRST_COMPLETED)
        for future in done:
            node = in_flight.pop(future); child = self._step_tokens.pop(future, None)
            if node.state is not StepState.RUNNING: continue
            try: ok, results, error = future.result()
            except Exception as exc: ok, results, error = False, [], f"executor error: {exc}"
            node.tool_results = results; node.result = results[-1] if results else None; node.completed_at = utcnow()
            timed_out = child is not None and child.cancelled and not (child._event.is_set()) if False else (child is not None and child.deadline is not None and time.monotonic() >= child.deadline)
            if timed_out and not ok:
                node.transition(StepState.TIMED_OUT); node.error = error or "step deadline exceeded"
                self._emit(EventType.STEP_TIMED_OUT, task_id, {"step_id": node.step.id, "timeout_s": node.timeout_s})
            elif ok:
                node.transition(StepState.SUCCEEDED); self._emit(EventType.STEP_COMPLETED, task_id, {"step_id": node.step.id})
            else:
                node.transition(StepState.FAILED); node.error = error
                self._emit(EventType.STEP_FAILED, task_id, {"step_id": node.step.id, "error": error})
            if context is not None: context.tool_results.extend(results)

    def _enforce_deadlines(self, graph: TaskGraph, task_id: str, in_flight: dict[Future, GraphNode]) -> None:
        """Request cancellation at the deadline; state changes only after the worker has stopped."""
        now = time.monotonic()
        for future, node in list(in_flight.items()):
            child = self._step_tokens.get(future)
            if node.state is not StepState.RUNNING or child is None or child.deadline is None: continue
            if now >= child.deadline: child.cancel()

    def _stop_unstarted(self, graph: TaskGraph, task_id: str, token: CancellationToken, halting: bool) -> None:
        reason = "cancel requested" if token.cancelled else "fail-fast halt"
        for node in graph.nodes:
            if node.state in (StepState.PENDING, StepState.READY):
                node.transition(StepState.CANCELLED); node.error = f"never started: {reason}"
                self._emit(EventType.STEP_CANCELLED, task_id, {"step_id": node.step.id, "reason": reason})

    def _drain_stuck(self, graph: TaskGraph, task_id: str) -> bool:
        stuck = [node for node in graph.nodes if node.state in (StepState.PENDING, StepState.READY)]
        if not stuck or graph.done(): return False
        for node in stuck:
            try: node.transition(StepState.SKIPPED)
            except DomainValidationError: continue
            node.error = "unsatisfiable dependencies; skipped defensively"; self._emit(EventType.STEP_SKIPPED, task_id, {"step_id": node.step.id})
        return True

    @staticmethod
    def _first_failure(graph: TaskGraph) -> bool:
        return any(node.state in (StepState.FAILED, StepState.TIMED_OUT) for node in graph.nodes)

    @staticmethod
    def _overall_status(graph: TaskGraph, token: CancellationToken) -> OverallStatus:
        if token.cancelled: return OverallStatus.CANCELLED
        if all(node.state is StepState.SUCCEEDED for node in graph.nodes): return OverallStatus.COMPLETED
        return OverallStatus.FAILED

    @staticmethod
    def _aggregate(task_id: str, graph: TaskGraph, status: OverallStatus, duration: float) -> ExecutionResult:
        buckets: dict[StepState, list[str]] = {state: [] for state in StepState}; errors: list[str] = []; states: dict[str, StepState] = {}; results: dict[str, list[ToolResult]] = {}
        for node in graph.nodes:
            buckets[node.state].append(node.step.id); states[node.step.id] = node.state; results[node.step.id] = list(node.tool_results)
            if node.error: errors.append(f"{node.step.id}: {node.error}")
        return ExecutionResult(task_id=task_id, status=status, states=states, succeeded=buckets[StepState.SUCCEEDED], failed=buckets[StepState.FAILED], cancelled=buckets[StepState.CANCELLED], timed_out=buckets[StepState.TIMED_OUT], skipped=buckets[StepState.SKIPPED], results=results, duration_s=duration, errors=errors)

    def _persist(self, graph: TaskGraph, context: ExecutionContext | None, contexts_repo: SqliteExecutionContextRepository | None) -> None:
        if context is None or contexts_repo is None: return
        context.metadata["graph"] = graph.snapshot(); contexts_repo.save(context)

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self._bus is not None: self._bus.publish(Event(event_type=event_type, task_id=task_id, payload=payload))
