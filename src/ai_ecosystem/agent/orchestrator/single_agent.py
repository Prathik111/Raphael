"""Single-agent PACE orchestration loop (Gate 14, first product milestone).

Flow (existing state machine, no competing one)::

    GOAL -> UNDERSTANDING -> AWARENESS -> RESEARCHING? -> PLANNING
      -> WAITING_PERMISSION -> EXECUTING -> VERIFYING -> RECOVERING?
      -> COMPLETED / FAILED

The orchestrator coordinates only. Persistence, authorization,
execution, verification, recovery, research, memory, and
personalization all stay in their home modules. Model output never
touches tools: understanding and planning produce data, and every tool
call still passes RiskEngine -> PolicyEngine -> AuthorizationManager.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal
from collections.abc import Callable

from pydantic import BaseModel, Field

from ai_ecosystem.agent.executor.executor import (
    ExecutionResult,
    OverallStatus,
    ParallelExecutor,
)
from ai_ecosystem.agent.executor.cancellation import CancellationToken
from ai_ecosystem.agent.executor.graph import TaskGraph
from ai_ecosystem.agent.planner.backend import ReasoningBackend
from ai_ecosystem.agent.recovery.engine import RecoveryEngine, RecoveryOutcome
from ai_ecosystem.agent.recovery.planner import RecoveryPlanner
from ai_ecosystem.agent.recovery.policy import RecoveryPolicy, RetryPolicy
from ai_ecosystem.agent.verifier.verifier import Verifier
from ai_ecosystem.core.errors.exceptions import ModelError
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.domain import (
    ExecutionContext,
    Plan,
    Task,
    VerificationResult,
)
from ai_ecosystem.core.models.enums import (
    EventType,
    MemoryScope,
    MemoryType,
    StepState,
    TaskState,
    VerificationStatus,
)
from ai_ecosystem.core.runtime.task_manager import AgentRuntime
from ai_ecosystem.intelligence.models.providers import ModelRequest, request_structured
from ai_ecosystem.intelligence.research.manager import ResearchManager
from ai_ecosystem.intelligence.research.models import ResearchQuery
from ai_ecosystem.intelligence.router.router import ModelRouter, RoutingRequirements
from ai_ecosystem.personalization.memory.models import MemoryCandidate
from ai_ecosystem.personalization.memory.store import MemoryStore
from ai_ecosystem.personalization.personality.engine import PersonalizationEngine
from ai_ecosystem.tools.registry.registry import ToolRegistry
from ai_ecosystem.tools.registry.runner import ToolRunner

AgentStatus = Literal["COMPLETED", "FAILED", "ESCALATED"]


class _Cancelled(Exception):
    """Operator cancellation observed at a phase boundary."""


_UNDERSTAND_SYSTEM = (
    "You convert a user goal into a structured task. Reply with exactly "
    "one JSON object: title, constraints[], desired_outcome, needs_research."
)


class TaskSpec(BaseModel):
    """Structured understanding of a user goal (model output, data only)."""

    title: str = ""
    constraints: list[str] = Field(default_factory=list)
    desired_outcome: str = ""
    needs_research: bool = False


class AgentConfig(BaseModel):
    """Per-run configuration (no secrets, no policy)."""

    model_config = {"arbitrary_types_allowed": True}

    workspace: str = ""
    project_id: str = ""
    agent_id: str = "single-agent"
    arguments: dict[str, dict[str, Any]] = Field(default_factory=dict)
    verification_criteria: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    verification_root: str = ""
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list)
    remember_summary: bool = True
    max_attempts: int = 3
    max_replans: int = 1


class AgentResult(BaseModel):
    """Structured final result (user-facing, no secrets)."""

    model_config = {"arbitrary_types_allowed": True}

    task_id: str = ""
    status: AgentStatus = "FAILED"
    summary: str = ""
    reply: str = ""
    step_states: dict[str, StepState] = Field(default_factory=dict)
    verification_status: str = VerificationStatus.PENDING.value
    recovery: RecoveryOutcome | None = None
    memory_ids: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict)
    error: str = ""


@dataclass
class _Clock:
    marks: dict[str, float] = field(default_factory=dict)

    def mark(self, name: str, started: float) -> None:
        self.marks[name] = round(time.monotonic() - started, 3)


class SingleAgent:
    """Coordinates Gates 3-13 subsystems through one PACE loop."""

    def __init__(
        self,
        runtime: AgentRuntime,
        router: ModelRouter,
        requirements: RoutingRequirements,
        registry: ToolRegistry,
        runner: ToolRunner,
        planner: ReasoningBackend,
        verifier: Verifier,
        recovery_planner_factory: Callable[[], RecoveryPlanner] | None = None,
        research: ResearchManager | None = None,
        memories: MemoryStore | None = None,
        personalization: PersonalizationEngine | None = None,
        executor_factory: Callable[[], ParallelExecutor] | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._runtime = runtime
        self._router = router
        self._requirements = requirements
        self._registry = registry
        self._runner = runner
        self._planner = planner
        self._verifier = verifier
        self._recovery_planner_factory = recovery_planner_factory
        self._research = research
        self._memories = memories
        self._personalization = personalization
        self._executor_factory = executor_factory
        self._bus = bus or runtime.bus
        self._config = AgentConfig()

    # -- public entry points --------------------------------------------

    def run_goal(
        self,
        goal_text: str,
        config: AgentConfig | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> AgentResult:
        """Run the full PACE loop for a user goal string."""
        self._config = config or AgentConfig()
        clock = _Clock()
        total_started = time.monotonic()
        task, ctx = self._runtime.manager.create_task(goal_text, goal_text)
        return self._drive(task, ctx, goal_text, clock, total_started, cancel_token)

    def run_task(
        self,
        task_id: str,
        config: AgentConfig | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> AgentResult:
        """Drive an already-created task through the full PACE loop.

        The desktop API creates the task first (so the UI has an id to
        poll), then dispatch calls this. Goal text comes from the stored
        execution context.
        """
        self._config = config or AgentConfig()
        clock = _Clock()
        total_started = time.monotonic()
        task = self._runtime.manager.get_task(task_id)
        ctx = self._runtime.manager.get_context(task_id)
        if task is None or ctx is None:
            raise ValueError(f"task {task_id!r} does not exist")
        goal_text = ctx.goal or task.title
        return self._drive(task, ctx, goal_text, clock, total_started, cancel_token)

    def _check_cancelled(self, task_id: str, cancel_token: CancellationToken | None) -> None:
        """Raise _Cancelled when the operator cancelled mid-run."""
        if cancel_token is not None and cancel_token.cancelled:
            raise _Cancelled(f"task {task_id} cancelled by operator")

    def _drive(
        self,
        task: Task,
        ctx: ExecutionContext,
        goal_text: str,
        clock: _Clock,
        total_started: float,
        cancel_token: CancellationToken | None = None,
    ) -> AgentResult:
        """Shared PACE loop; the task always terminates (COMPLETED/FAILED)."""
        try:
            self._check_cancelled(task.id, cancel_token)
            spec = self._understand(task, ctx, goal_text, clock)
            self._check_cancelled(task.id, cancel_token)
            self._aware(task, ctx, clock)
            if spec.needs_research:
                self._research_phase(task, ctx, goal_text, clock)
            self._check_cancelled(task.id, cancel_token)
            plan = self._plan(
                task, ctx, goal_text, clock, context=self._recall(task, ctx, goal_text, clock)
            )
            execution = self._execute(task, ctx, plan, clock, cancel_token)
            self._check_cancelled(task.id, cancel_token)
            verifications = self._verify(task, ctx, plan, execution, clock)
            recovery = self._recover_if_needed(
                task, ctx, plan, execution, verifications, clock, cancel_token
            )
            if recovery is not None:
                verifications = self._reverify(recovery)
            memory_ids = self._memorize(task, ctx, clock)
            return self._complete(
                task,
                ctx,
                plan,
                execution,
                verifications,
                recovery,
                memory_ids,
                clock,
                total_started,
            )
        except _Cancelled as exc:
            return self._cancelled(task, ctx, str(exc), clock, total_started)
        except Exception as exc:  # noqa: BLE001 -- task must always terminate
            # A failure racing a cancel is a cancel: the operator spoke
            # last, so the result must say so.
            if cancel_token is not None and cancel_token.cancelled:
                return self._cancelled(
                    task, ctx, f"task {task.id} cancelled by operator", clock, total_started
                )
            from ai_ecosystem.core.secrets import sanitize_exception

            return self._fail(task, ctx, sanitize_exception(exc), clock, total_started)

    def resume(self, task_id: str, config: AgentConfig | None = None) -> AgentResult:
        """Resume a persisted task that already has a plan (crash recovery)."""
        self._config = config or AgentConfig()
        clock = _Clock()
        total_started = time.monotonic()
        task = self._runtime.manager.get_task(task_id)
        ctx = self._runtime.manager.get_context(task_id)
        if task is None or ctx is None or ctx.plan is None:
            raise ValueError(f"task {task_id!r} has no resumable plan")
        try:
            self._walk_to(task, TaskState.EXECUTING)
            execution = self._execute(task, ctx, ctx.plan, clock)
            verifications = self._verify(task, ctx, ctx.plan, execution, clock)
            recovery = self._recover_if_needed(task, ctx, ctx.plan, execution, verifications, clock)
            if recovery is not None:
                verifications = self._reverify(recovery)
            memory_ids = self._memorize(task, ctx, clock)
            return self._complete(
                task,
                ctx,
                ctx.plan,
                execution,
                verifications,
                recovery,
                memory_ids,
                clock,
                total_started,
            )
        except Exception as exc:  # noqa: BLE001
            from ai_ecosystem.core.secrets import sanitize_exception

            return self._fail(task, ctx, sanitize_exception(exc), clock, total_started)

    # -- phases ----------------------------------------------------------

    def _understand(
        self, task: Task, ctx: ExecutionContext, goal_text: str, clock: _Clock
    ) -> TaskSpec:
        started = time.monotonic()
        self._runtime.manager.transition(task.id, TaskState.UNDERSTANDING)
        provider = self._router.select(self._requirements)
        try:
            spec = request_structured(
                provider,
                ModelRequest(prompt=f"UNDERSTAND: {goal_text}", system=_UNDERSTAND_SYSTEM),
                TaskSpec,
            )
        except ModelError as exc:
            raise RuntimeError(f"understanding failed: {exc}") from exc
        ctx.metadata["task_spec"] = spec.model_dump()
        self._runtime.contexts_repo.save(ctx)
        self._emit(
            EventType.UNDERSTANDING_COMPLETED, task.id, {"needs_research": spec.needs_research}
        )
        clock.mark("understanding_s", started)
        return spec

    def _aware(self, task: Task, ctx: ExecutionContext, clock: _Clock) -> None:
        started = time.monotonic()
        self._runtime.manager.transition(task.id, TaskState.AWARENESS)
        tools = sorted(tool.name for tool in self._registry.list_tools())
        ctx.observations.append(f"available tools: {', '.join(tools)}")
        workspace = self._config.workspace
        if workspace and self._registry.has("filesystem.list"):
            call = self._registry.build_call(task.id, "filesystem.list", {"path": workspace})
            result = self._runner.run(call)
            if result.success:
                ctx.observations.append(f"workspace entries: {result.output}")
            else:
                ctx.observations.append(f"workspace unavailable: {result.error}")
        self._runtime.contexts_repo.save(ctx)
        self._emit(EventType.AWARENESS_COMPLETED, task.id, {"tools": len(tools)})
        clock.mark("awareness_s", started)

    def _research_phase(
        self, task: Task, ctx: ExecutionContext, goal_text: str, clock: _Clock
    ) -> None:
        started = time.monotonic()
        self._runtime.manager.transition(task.id, TaskState.RESEARCHING)
        if self._research is None:
            ctx.observations.append("research requested but no manager configured")
        else:
            result = self._research.research(ResearchQuery(query=goal_text))
            for item in result.evidence:
                ctx.observations.append(f"evidence [{item.source_id[:8]}]: {item.claim}")
            ctx.metadata["research_confidence"] = result.confidence
            ctx.metadata["research_conflicts"] = len(result.conflicts)
        self._runtime.contexts_repo.save(ctx)
        clock.mark("research_s", started)

    def _recall(self, task: Task, ctx: ExecutionContext, goal_text: str, clock: _Clock) -> str:
        """Load relevant memories from previous tasks (session continuity).

        Retrieval is scoped (GLOBAL + configured PROJECT/AGENT) and the
        hits land in the context observations AND the returned text, so
        both the audit trail and the planner see them. Without this,
        every conversation starts blank.
        """
        started = time.monotonic()
        try:
            if self._memories is None:
                return ""
            project_id = self._config.project_id
            recalled = self._memories.retrieve(MemoryScope.GLOBAL, query=goal_text, limit=5)
            if project_id:
                recalled = recalled + self._memories.retrieve(
                    MemoryScope.PROJECT, project_id, query=goal_text, project_id=project_id, limit=5
                )
            if self._config.agent_id:
                recalled = recalled + self._memories.retrieve(
                    MemoryScope.AGENT, self._config.agent_id, query=goal_text, limit=5
                )
            seen: set[str] = set()
            unique = []
            for memory in recalled:
                if memory.id not in seen:
                    seen.add(memory.id)
                    unique.append(memory)
            lines = []
            for memory in unique[:8]:
                line = f"recalled memory [{memory.scope.value}]: {memory.content}"
                ctx.observations.append(line)
                lines.append(line)
            self._runtime.contexts_repo.save(ctx)
            self._emit(EventType.MEMORY_RECALLED, task.id, {"count": len(lines)})
            return "\n".join(lines)
        finally:
            clock.mark("recall_s", started)

    def _plan(
        self, task: Task, ctx: ExecutionContext, goal_text: str, clock: _Clock, context: str = ""
    ) -> Plan:
        started = time.monotonic()
        current = self._runtime.manager.get_task(task.id).state
        if current is not TaskState.PLANNING:
            self._runtime.manager.transition(task.id, TaskState.PLANNING)
        known = [tool.name for tool in self._registry.list_tools()]
        plan = self._planner.plan(goal_text, known, context=context)
        ctx.plan = plan
        self._runtime.contexts_repo.save(ctx)
        self._emit(EventType.PLAN_CREATED, task.id, {"steps": len(plan.steps), "goal": plan.goal})
        clock.mark("planning_s", started)
        return plan

    def _execute(
        self,
        task: Task,
        ctx: ExecutionContext,
        plan: Plan,
        clock: _Clock,
        cancel_token: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = time.monotonic()
        self._walk_to(task, TaskState.EXECUTING)
        execution = self._executor().execute_graph(
            task.id,
            TaskGraph.from_plan(plan),
            arguments=self._config.arguments,
            context=ctx,
            contexts_repo=self._runtime.contexts_repo,
            cancel=cancel_token,
        )
        clock.mark("execution_s", started)
        return execution

    def _verify(
        self,
        task: Task,
        ctx: ExecutionContext,
        plan: Plan,
        execution: ExecutionResult,
        clock: _Clock,
    ) -> dict[str, VerificationResult]:
        started = time.monotonic()
        self._walk_to(task, TaskState.VERIFYING)
        out: dict[str, VerificationResult] = {}
        for step in plan.steps:
            out[step.id] = self._verify_one(task.id, step.id, execution.results.get(step.id, []))
        clock.mark("verification_s", started)
        return out

    def _recover_if_needed(
        self,
        task: Task,
        ctx: ExecutionContext,
        plan: Plan,
        execution: ExecutionResult,
        verifications: dict[str, VerificationResult],
        clock: _Clock,
        cancel_token: CancellationToken | None = None,
    ) -> RecoveryOutcome | None:
        started = time.monotonic()
        try:
            if execution.status is OverallStatus.COMPLETED and all(
                v.status is VerificationStatus.PASSED for v in verifications.values()
            ):
                return None
            self._runtime.manager.transition(task.id, TaskState.RECOVERING)
            self._emit(EventType.RECOVERY_STARTED, task.id, {})
            planner = self._recovery_planner_factory() if self._recovery_planner_factory else None
            policy = RecoveryPolicy(
                retries=RetryPolicy(max_attempts=self._config.max_attempts),
                max_replans=self._config.max_replans,
            )
            engine = RecoveryEngine(planner=planner, policy=policy, bus=self._bus)

            def execute_fn(current: Plan, args: dict) -> ExecutionResult:
                return self._executor().execute_graph(
                    task.id,
                    TaskGraph.from_plan(current),
                    arguments=args or self._config.arguments,
                    context=ctx,
                    contexts_repo=self._runtime.contexts_repo,
                    cancel=cancel_token,
                )

            def verify_fn(
                task_id: str, current: Plan, result: ExecutionResult
            ) -> VerificationResult:
                merged = [
                    self._verify_step(task_id, step.id, result.results.get(step.id, []))
                    for step in current.steps
                ]
                return self._worst(task_id, merged)

            outcome = engine.run_with_prior(
                task.id,
                plan,
                execute_fn,
                verify_fn,
                self._config.arguments,
                self._config.max_attempts,
                prior_result=execution,
                prior_verification=self._worst(task.id, list(verifications.values())),
            )
            self._runtime.manager.transition(task.id, TaskState.EXECUTING)
            self._runtime.manager.transition(task.id, TaskState.VERIFYING)
            return outcome
        finally:
            clock.mark("recovery_s", started)

    def _memorize(self, task: Task, ctx: ExecutionContext, clock: _Clock) -> list[str]:
        started = time.monotonic()
        try:
            if self._memories is None:
                return []
            ids: list[str] = []
            for candidate in self._config.memory_candidates:
                if candidate.scope.value == "task" and not candidate.scope_id:
                    candidate.scope_id = task.id
                if candidate.scope.value == "project" and not candidate.scope_id:
                    candidate.scope_id = self._config.project_id
                try:
                    ids.append(self._memories.store(candidate).id)
                except Exception:  # noqa: BLE001 -- memory must not fail tasks
                    continue
            if self._config.remember_summary and ctx.plan is not None:
                try:
                    # Project-scoped when the run belongs to a project so
                    # LATER tasks can recall it; task-scoped otherwise
                    # (visible only to this task, preserving isolation).
                    project_id = self._config.project_id
                    if project_id:
                        scope, scope_id = MemoryScope.PROJECT, project_id
                    else:
                        scope, scope_id = MemoryScope.TASK, task.id
                    summary = MemoryCandidate(
                        content=(
                            f"Task '{task.title}' (goal: {ctx.goal}); "
                            f"{len(ctx.tool_results)} tool results recorded."
                        ),
                        type=MemoryType.EPISODIC,
                        source=f"task:{task.id}",
                        confidence=0.9,
                        importance=0.6,
                        scope=scope,
                        scope_id=scope_id,
                        reason="automatic task summary",
                    )
                    ids.append(self._memories.store(summary).id)
                except Exception:  # noqa: BLE001
                    pass
            return ids
        finally:
            clock.mark("memory_s", started)

    def _store_result(self, ctx: ExecutionContext, result: AgentResult) -> None:
        """Persist the result on the context so the API can serve it."""
        try:
            ctx.metadata["agent_result"] = result.model_dump(mode="json")
        except Exception:  # noqa: BLE001 -- persistence degrades, never fails
            ctx.metadata["agent_result"] = {
                "task_id": result.task_id,
                "status": result.status,
                "summary": result.summary,
                "error": result.error,
            }

    def _complete(
        self,
        task: Task,
        ctx: ExecutionContext,
        plan: Plan,
        execution: ExecutionResult,
        verifications: dict[str, VerificationResult],
        recovery: RecoveryOutcome | None,
        memory_ids: list[str],
        clock: _Clock,
        total_started: float,
    ) -> AgentResult:
        current = self._runtime.manager.get_task(task.id)
        if current is not None and current.state is TaskState.CANCELLED:
            return self._cancelled(task, ctx, "cancelled by operator", clock, total_started)
        worst = self._worst(task.id, list(verifications.values()))
        recovered = recovery is not None and recovery.status.value == "RECOVERED"
        if (
            execution.status is OverallStatus.COMPLETED
            and worst.status is VerificationStatus.PASSED
        ):
            status: AgentStatus = "COMPLETED"
            self._runtime.manager.transition(task.id, TaskState.COMPLETED)
            self._emit(EventType.TASK_COMPLETED, task.id, {})
        elif recovered:
            status = "COMPLETED"
            self._runtime.manager.transition(task.id, TaskState.COMPLETED)
            self._emit(EventType.TASK_COMPLETED, task.id, {"recovered": True})
        elif recovery is not None and recovery.status.value == "ESCALATED":
            status = "ESCALATED"
            self._runtime.manager.transition(task.id, TaskState.FAILED)
            self._emit(EventType.TASK_FAILED, task.id, {"escalated": True})
        else:
            status = "FAILED"
            self._runtime.manager.transition(task.id, TaskState.FAILED)
            self._emit(EventType.TASK_FAILED, task.id, {})
        summary = self._summarize(ctx, execution, worst, recovery, task)
        clock.marks["total_s"] = round(time.monotonic() - total_started, 3)
        result = AgentResult(
            task_id=task.id,
            status=status,
            summary=summary,
            reply=self._collect_reply(plan, execution),
            step_states=dict(execution.states),
            verification_status=worst.status.value,
            recovery=recovery,
            memory_ids=memory_ids,
            evidence=[o for o in ctx.observations if o.startswith("evidence ")],
            timings=dict(clock.marks),
            error="" if status == "COMPLETED" else worst.reason,
        )
        self._store_result(ctx, result)
        self._runtime.contexts_repo.save(ctx)
        return result

    def _cancelled(
        self, task: Task, ctx: ExecutionContext, error: str, clock: _Clock, total_started: float
    ) -> AgentResult:
        """Settle a cancelled run without fighting the cancel transition."""
        try:
            current = self._runtime.manager.get_task(task.id)
            if current is not None and current.state not in (
                TaskState.COMPLETED,
                TaskState.FAILED,
                TaskState.CANCELLED,
            ):
                self._runtime.manager.transition(task.id, TaskState.CANCELLED)
        finally:
            clock.marks["total_s"] = round(time.monotonic() - total_started, 3)
            result = AgentResult(
                task_id=task.id,
                status="FAILED",
                summary="Cancelled by the operator before " "completion.",
                timings=dict(clock.marks),
                error=error,
            )
            self._store_result(ctx, result)
            self._runtime.contexts_repo.save(ctx)
        return result

    def _fail(
        self, task: Task, ctx: ExecutionContext, error: str, clock: _Clock, total_started: float
    ) -> AgentResult:
        try:
            current = self._runtime.manager.get_task(task.id)
            if current is not None and current.state not in (
                TaskState.COMPLETED,
                TaskState.FAILED,
                TaskState.CANCELLED,
            ):
                self._runtime.manager.transition(task.id, TaskState.FAILED)
        finally:
            self._emit(EventType.TASK_FAILED, task.id, {"error": error})
            clock.marks["total_s"] = round(time.monotonic() - total_started, 3)
            result = AgentResult(
                task_id=task.id, status="FAILED", summary="", timings=dict(clock.marks), error=error
            )
            self._store_result(ctx, result)
            self._runtime.contexts_repo.save(ctx)
        return result

    # -- helpers ----------------------------------------------------------

    def _executor(self) -> ParallelExecutor:
        if self._executor_factory is None:
            raise RuntimeError("no executor factory configured")
        return self._executor_factory()

    def _verify_one(self, task_id: str, step_id: str, results: list) -> VerificationResult:
        criteria = self._config.verification_criteria.get(
            step_id, [{"strategy": "command_results"}]
        )
        return self._verifier.verify(
            task_id, step_id, results, criteria, root=self._config.verification_root
        )

    def _verify_step(self, task_id: str, step_id: str, results: list) -> VerificationResult:
        return self._verify_one(task_id, step_id, results)

    @staticmethod
    def _worst(task_id: str, results: list[VerificationResult]) -> VerificationResult:
        order = {
            VerificationStatus.FAILED: 0,
            VerificationStatus.ERROR: 1,
            VerificationStatus.INCONCLUSIVE: 2,
            VerificationStatus.PASSED: 3,
            VerificationStatus.PENDING: 4,
        }
        if not results:
            return VerificationResult(
                task_id=task_id,
                status=VerificationStatus.INCONCLUSIVE,
                reason="nothing was verified",
            )
        return sorted(results, key=lambda r: order.get(r.status, 5))[0]

    def _reverify(self, recovery: RecoveryOutcome) -> dict[str, VerificationResult]:
        if recovery.final_verification is not None:
            return {"all": recovery.final_verification}
        return {}

    def _collect_reply(self, plan: Plan, execution: ExecutionResult) -> str:
        """Assemble the user-facing reply from agent.respond outputs.

        Steps name their tools, so outputs of respond steps are the
        model's own words -- safe to surface, unlike file contents.
        """
        parts: list[str] = []
        for step in plan.steps:
            if "agent.respond" not in step.tools:
                continue
            for result in execution.results.get(step.id, []):
                if result.success and isinstance(result.output, str):
                    text = result.output.strip()
                    if text:
                        parts.append(text)
        return "\n\n".join(parts)

    def _summarize(
        self,
        ctx: ExecutionContext,
        execution: ExecutionResult,
        worst: VerificationResult,
        recovery: RecoveryOutcome | None,
        task: Task,
    ) -> str:
        lines = [
            f"Task '{task.title}' ended {execution.status.value}; "
            f"verification {worst.status.value}.",
            f"Steps succeeded: {len(execution.succeeded)}/{len(execution.states)}.",
        ]
        if recovery is not None:
            actions = ", ".join(f"{r.action.value}#{r.attempt}" for r in recovery.audit)
            lines.append(f"Recovery: {recovery.status.value} ({actions}).")
        if worst.reason:
            lines.append(f"Verification note: {worst.reason}")
        detail = "\n".join(lines)
        if self._personalization is not None:
            context = self._personalization.build_context(
                task_id=task.id,
                project_id=self._config.project_id,
                agent_id=self._config.agent_id,
                query=ctx.goal,
            )
            return self._personalization.summarize(context, detail)
        return detail

    def _walk_to(self, task: Task, target: TaskState) -> None:
        """Walk the state machine to target along legal edges."""
        _PATHS: dict[tuple[TaskState, TaskState], list[TaskState]] = {
            (TaskState.VERIFYING, TaskState.EXECUTING): [TaskState.RECOVERING, TaskState.EXECUTING],
            (TaskState.RECOVERING, TaskState.EXECUTING): [TaskState.EXECUTING],
            (TaskState.EXECUTING, TaskState.VERIFYING): [TaskState.VERIFYING],
            (TaskState.RECOVERING, TaskState.VERIFYING): [TaskState.EXECUTING, TaskState.VERIFYING],
        }
        _FORWARD = [
            TaskState.CREATED,
            TaskState.UNDERSTANDING,
            TaskState.AWARENESS,
            TaskState.PLANNING,
            TaskState.WAITING_PERMISSION,
            TaskState.EXECUTING,
            TaskState.VERIFYING,
        ]
        current = self._runtime.manager.get_task(task.id).state
        if current is target:
            return
        path = _PATHS.get((current, target))
        if path is None and current in _FORWARD and target in _FORWARD:
            start, end = _FORWARD.index(current), _FORWARD.index(target)
            if end > start:
                path = _FORWARD[start + 1 : end + 1]
        if path is None:
            self._runtime.manager.transition(task.id, target)
            return
        for state in path:
            self._runtime.manager.transition(task.id, state)

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        self._bus.publish(Event(event_type=event_type, task_id=task_id, payload=payload))
