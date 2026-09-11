"""Agent registry + manager over the shared runtime (Gate 18).

All agents reuse ONE AgentRuntime, ONE ToolRegistry, and the normal
executor/verifier/recovery machinery. What differs per agent is the
authorization scope: each run builds an AuthorizationManager whose
policy confines that agent to its own allow-list. Agents therefore
cannot inherit, grant, or borrow permissions.
"""

from __future__ import annotations

import re
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any
from collections.abc import Callable

from ai_ecosystem.agent.executor.cancellation import CancellationToken
from ai_ecosystem.agent.executor.executor import (
    ExecutionResult,
    OverallStatus,
    ParallelExecutor,
)
from ai_ecosystem.agent.multi.definitions import (
    AgentDefinition,
    AgentStatus,
    AgentTask,
)
from ai_ecosystem.core.errors.exceptions import (
    DomainValidationError,
    ResourceNotFoundError,
)
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.enums import EventType
from ai_ecosystem.core.persistence.sqlite import Database, _SnapshotTable
from ai_ecosystem.core.runtime.task_manager import AgentRuntime
from ai_ecosystem.security.policy.engines import (
    AuthorizationManager,
    Policy,
    PolicyEngine,
    RiskContext,
)
from ai_ecosystem.tools.registry.registry import ToolRegistry
from ai_ecosystem.tools.registry.runner import ToolRunner

def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


class AgentRegistry:
    """Persisted agent definitions (duplicate ids rejected)."""

    def __init__(self, db: Database, bus: EventBus | None = None) -> None:
        self._table = _SnapshotTable(db, "agent_definitions", AgentDefinition)
        self._bus = bus

    def register(self, definition: AgentDefinition) -> AgentDefinition:
        """Store a new agent (duplicate ids rejected)."""
        if not definition.name:
            raise DomainValidationError("agent name must not be empty")
        if self._table.get(definition.id):
            raise DomainValidationError(f"duplicate agent id {definition.id!r}")
        created = self._table.create(definition)
        self._emit(EventType.AGENT_REGISTERED, "",
                   {"agent_id": created.id, "role": created.role})
        return created

    def lookup(self, agent_id: str) -> AgentDefinition | None:
        """Fetch by id (None when unknown)."""
        return self._table.get(agent_id)

    def by_role(self, role: str) -> list[AgentDefinition]:
        """All agents with a role, sorted by name."""
        return sorted(
            [d for d in self._table.list() if d.role == role],
            key=lambda d: d.name,
        )

    def list(self) -> list[AgentDefinition]:
        """All definitions sorted by name."""
        return sorted(self._table.list(), key=lambda d: d.name)

    def set_status(self, agent_id: str, status: AgentStatus) -> AgentDefinition:
        """Move lifecycle state (no validation: manager drives order)."""
        definition = self._table.get(agent_id)
        if definition is None:
            raise ResourceNotFoundError("AgentDefinition", agent_id)
        definition.status = status
        definition.touch()
        return self._table.update(definition)

    def deregister(self, agent_id: str) -> bool:
        """Remove a definition (tasks keep their owner history)."""
        return self._table.delete(agent_id)

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(event_type=event_type, task_id=task_id, payload=payload))


class AgentTaskRepository:
    """Persisted agent tasks (ownership survives restart)."""

    def __init__(self, db: Database) -> None:
        self._table = _SnapshotTable(db, "agent_tasks", AgentTask)

    def create(self, item: AgentTask) -> AgentTask:
        """Persist a new agent task."""
        return self._table.create(item)

    def get(self, item_id: str) -> AgentTask | None:
        """Fetch by record id."""
        return self._table.get(item_id)

    def by_task(self, task_id: str) -> AgentTask | None:
        """Fetch by runtime task id."""
        for item in self._table.list():
            if item.task_id == task_id:
                return item
        return None

    def update(self, item: AgentTask) -> AgentTask:
        """Replace the stored record."""
        return self._table.update(item)

    def list(self) -> list[AgentTask]:
        """All records in creation order."""
        return sorted(self._table.list(), key=lambda item: item.created_at)


class AgentManager:
    """Spawns agents and runs their tasks on the shared runtime."""

    def __init__(
        self,
        agents: AgentRegistry,
        runtime: AgentRuntime,
        tools: ToolRegistry,
        max_workers: int = 4,
        bus: EventBus | None = None,
    ) -> None:
        if max_workers < 1:
            raise DomainValidationError("max_workers must be >= 1")
        self._agents = agents
        self._runtime = runtime
        self._tools = tools
        self._tasks = AgentTaskRepository(runtime.db)
        self._max_workers = max_workers
        self._bus = bus or runtime.bus

    @property
    def task_repository(self) -> AgentTaskRepository:
        """Task records (restart recovery reads through here)."""
        return self._tasks

    def spawn(self, agent_id: str) -> AgentDefinition:
        """Bring an agent to READY (CREATED -> READY)."""
        definition = self._agents.lookup(agent_id)
        if definition is None:
            raise ResourceNotFoundError("AgentDefinition", agent_id)
        if definition.status is not AgentStatus.CREATED:
            raise DomainValidationError(f"agent {agent_id!r} already spawned")
        return self._agents.set_status(agent_id, AgentStatus.READY)

    def submit(self, agent_id: str, goal: str, plan: Any,
               arguments: dict | None = None,
               parent_agent: str = "", parent_task: str = "") -> AgentTask:
        """Create a runtime task owned by one agent (with parentage)."""
        definition = self._agents.lookup(agent_id)
        if definition is None:
            raise ResourceNotFoundError("AgentDefinition", agent_id)
        if definition.status not in (AgentStatus.READY, AgentStatus.RUNNING):
            raise DomainValidationError(f"agent {agent_id!r} is not runnable")
        task, _ = self._runtime.manager.create_task(f"[{definition.name}] {goal}", goal)
        record = self._tasks.create(AgentTask(
            task_id=task.id, owner_agent=agent_id,
            parent_agent=parent_agent, parent_task=parent_task,
            goal=goal, plan=plan, arguments=dict(arguments or {}),
        ))
        return record

    def scoped_runner(self, agent_id: str) -> tuple[ToolRunner, AuthorizationManager]:
        """Runner + authorizer confined to one agent's allow-list."""
        definition = self._agents.lookup(agent_id)
        if definition is None:
            raise ResourceNotFoundError("AgentDefinition", agent_id)
        policy = Policy(
            name=f"agent:{agent_id}",
            auto_grant_up_to=definition.risk_profile,
            agent_scopes={agent_id: set(definition.allowed_tools)},
            strict_agent_scopes=True,
        )
        authorizer = AuthorizationManager(
            self._tools, policy_engine=PolicyEngine(policy),
            context=RiskContext(agent_id=agent_id))
        return ToolRunner(self._tools, authorizer, self._bus), authorizer

    def run_task(self, record_id: str,
                 cancel: CancellationToken | None = None) -> ExecutionResult:
        """Execute one submitted task with the owner's scoped runner."""
        record = self._tasks.get(record_id)
        if record is None:
            raise ResourceNotFoundError("AgentTask", record_id)
        if record.status not in (AgentStatus.CREATED, AgentStatus.READY):
            raise DomainValidationError("agent task already ran")
        runner, _ = self.scoped_runner(record.owner_agent)
        self._set_task_status(record, AgentStatus.RUNNING)
        self._agents.set_status(record.owner_agent, AgentStatus.RUNNING)
        self._emit(EventType.AGENT_STARTED, record.task_id,
                   {"agent_id": record.owner_agent})
        try:
            executor = ParallelExecutor(runner, self._tools, self._bus)
            result = executor.execute(
                record.task_id, record.plan, arguments=record.arguments,
                cancel=cancel,
            )
        except Exception as exc:  # noqa: BLE001 -- record the failure, don't strand it
            result = ExecutionResult(
                task_id=record.task_id, status=OverallStatus.FAILED,
                errors=[f"execution raised {type(exc).__name__}: {exc}"])
        finally:
            self._agents.set_status(record.owner_agent, AgentStatus.READY)
        ok = result.status.value == "COMPLETED"
        self._set_task_status(
            record, AgentStatus.COMPLETED if ok else AgentStatus.FAILED)
        record.result_summary = (
            f"{len(result.succeeded)} succeeded, {len(result.failed)} failed")
        self._tasks.update(record)
        self._emit(EventType.AGENT_COMPLETED, record.task_id,
                   {"agent_id": record.owner_agent, "success": ok})
        return result

    def run_all(self, record_ids: list[str],
                cancel: CancellationToken | None = None) -> dict[str, ExecutionResult]:
        """Run submitted tasks concurrently (bounded by max_workers)."""
        token = cancel or CancellationToken()
        pending = list(record_ids)
        in_flight: dict[Any, str] = {}
        results: dict[str, ExecutionResult] = {}
        pool = ThreadPoolExecutor(max_workers=self._max_workers)
        try:
            while pending or in_flight:
                while pending and len(in_flight) < self._max_workers:
                    if token.cancelled:
                        pending.clear()
                        break
                    record_id = pending.pop(0)
                    future = pool.submit(self.run_task, record_id, token)
                    in_flight[future] = record_id
                if not in_flight:
                    break
                done, _ = wait(set(in_flight), return_when=FIRST_COMPLETED)
                for future in done:
                    record_id = in_flight.pop(future)
                    try:
                        results[record_id] = future.result()
                    except Exception as exc:  # noqa: BLE001 -- per-task outcome
                        record = self._tasks.get(record_id)
                        task_id = record.task_id if record else ""
                        results[record_id] = ExecutionResult(
                            task_id=task_id, status=OverallStatus.FAILED,
                            errors=[f"agent run raised {type(exc).__name__}: {exc}"])
        finally:
            pool.shutdown(wait=True)
        return results

    def resume_interrupted(self) -> list[AgentTask]:
        """Reset RUNNING tasks to READY+interrupted after a restart."""
        reset = []
        for record in self._tasks.list():
            if record.status is AgentStatus.RUNNING:
                record.status = AgentStatus.READY
                record.interrupted = True
                self._tasks.update(record)
                reset.append(record)
        return reset

    def _set_task_status(self, record: AgentTask, status: AgentStatus) -> None:
        record.status = status
        self._tasks.update(record)

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        self._bus.publish(
            Event(event_type=event_type, task_id=task_id, payload=payload))


DecomposeFn = Callable[[str, list[AgentDefinition]], list["SubtaskSpec"]]


class SubtaskSpec:
    """One delegated unit: who does what with which plan."""

    def __init__(self, agent_id: str, goal: str, plan: Any,
                 arguments: dict | None = None) -> None:
        self.agent_id = agent_id
        self.goal = goal
        self.plan = plan
        self.arguments = dict(arguments or {})


class SupervisorResult:
    """Aggregated outcome of a supervised multi-agent run."""

    def __init__(self, task_ids: list[str],
                 results: dict[str, ExecutionResult]) -> None:
        self.task_ids = list(task_ids)
        self.results = dict(results)

    @property
    def succeeded(self) -> list[str]:
        """Task ids whose execution fully completed."""
        return [tid for tid, result in self.results.items()
                if result.status.value == "COMPLETED"]

    @property
    def failed(self) -> list[str]:
        """Task ids that did not fully complete."""
        return [tid for tid in self.task_ids if tid not in self.succeeded]


class Supervisor:
    """Decomposes goals, delegates to agents, aggregates verified results."""

    def __init__(
        self,
        manager: AgentManager,
        verifier: Any = None,
        bus: EventBus | None = None,
        decompose_fn: DecomposeFn | None = None,
    ) -> None:
        self._manager = manager
        self._verifier = verifier
        self._bus = bus
        self._decompose_fn = decompose_fn or self._default_decompose

    def decompose(self, goal: str) -> list[SubtaskSpec]:
        """Split a goal using the configured deterministic strategy."""
        agents = [d for d in self._manager._agents.list()
                  if d.status in (AgentStatus.READY, AgentStatus.RUNNING)]
        return list(self._decompose_fn(goal, agents))

    def run_goal(self, goal: str, specs: list[SubtaskSpec] | None = None,
                 cancel: CancellationToken | None = None) -> SupervisorResult:
        """Delegate subtasks, run them concurrently, aggregate outcomes."""
        chosen = specs if specs is not None else self.decompose(goal)
        if not chosen:
            raise DomainValidationError("supervisor produced no subtasks")
        records = [self._manager.submit(spec.agent_id, spec.goal, spec.plan,
                                        spec.arguments)
                   for spec in chosen]
        record_ids = [record.id for record in records]
        task_ids = [record.task_id for record in records]
        by_record = self._manager.run_all(record_ids, cancel)
        results = {record.task_id: by_record[record.id] for record in records}
        return SupervisorResult(task_ids, results)

    def verify_results(self, supervisor_result: SupervisorResult,
                       criteria: dict[str, list] | None = None) -> dict[str, Any]:
        """Verify each child result; never trust them blindly."""
        if self._verifier is None:
            raise DomainValidationError("supervisor has no verifier configured")
        verdicts = {}
        for task_id in supervisor_result.task_ids:
            result = supervisor_result.results[task_id]
            step_criteria = (criteria or {}).get(task_id, [{"strategy": "command_results"}])
            verdicts[task_id] = self._verifier.verify(
                task_id, "supervisor-review", result.all_tool_results(), step_criteria)
        return verdicts

    @staticmethod
    def _default_decompose(goal: str,
                           agents: list[AgentDefinition]) -> list[SubtaskSpec]:
        """Fallback: whole goal to the agent with best capability overlap."""
        wants = _keywords(goal)
        scored = sorted(
            agents,
            key=lambda d: (-len(wants & _keywords(" ".join(d.capabilities))), d.name),
        )
        if not scored:
            return []
        best = scored[0]
        return [SubtaskSpec(best.id, goal, best_fallback_plan(best))]


def best_fallback_plan(agent: AgentDefinition) -> Any:
    """Minimal single-tool plan from an agent's first allowed tool."""
    from ai_ecosystem.core.models.domain import Plan, PlanStep

    tool = agent.allowed_tools[0] if agent.allowed_tools else "noop"
    return Plan(
        goal=f"fallback for {agent.name}",
        steps=[PlanStep(id="s1", description=f"run {tool}", dependencies=[],
                        tools=[tool], verification="v", completion_criteria="c")],
        final_verification="v",
    )
