"""Repeatable benchmark suite: real measurements, machine output (Gate 41).

Every number comes from actually running the subsystem on this
machine; nothing is fabricated or copied. Budgets are generous
smoke bounds (pathology guards), not performance targets.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class BenchmarkResult:
    """One measured operation (all fields machine-generated)."""

    name: str = ""
    category: str = ""
    iterations: int = 0
    total_s: float = 0.0
    average_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkReport:
    """Whole-suite output (serializable to JSON)."""

    results: list[BenchmarkResult] = field(default_factory=list)
    generated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable mapping."""
        return {"generated_at": self.generated_at,
                "results": [vars(result) for result in self.results]}

    def write_json(self, path: str) -> str:
        """Persist the report; returns the path."""
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2)
        return path


def measure(name: str, category: str, iterations: int,
            fn: Callable[[int], Any], extra: dict | None = None) -> BenchmarkResult:
    """Time N real executions of fn(i); no warmup lies (all runs count)."""
    if iterations < 1:
        raise ValueError("iterations must be >= 1")
    samples: list[float] = []
    for index in range(iterations):
        started = time.monotonic()
        fn(index)
        samples.append((time.monotonic() - started) * 1000.0)
    total = sum(samples) / 1000.0
    return BenchmarkResult(
        name=name, category=category, iterations=iterations, total_s=round(total, 3),
        average_ms=round(statistics.fmean(samples), 3),
        min_ms=round(min(samples), 3), max_ms=round(max(samples), 3),
        extra=dict(extra or {}))


class BenchmarkRunner:
    """Registry of named benchmark cases with a shared context."""

    def __init__(self) -> None:
        self._cases: list[tuple[str, str, int, Callable]] = []
        self.context: dict[str, Any] = {}

    def add(self, name: str, category: str, iterations: int,
            fn: Callable[[dict, int], Any]) -> None:
        """Register a case (fn receives (context, iteration))."""
        self._cases.append((name, category, iterations, fn))

    def run(self) -> BenchmarkReport:
        """Execute every case in registration order."""
        report = BenchmarkReport(generated_at=time.time())
        for name, category, iterations, fn in self._cases:
            report.results.append(measure(
                name, category, iterations, lambda i: fn(self.context, i)))
        return report


def default_suite() -> BenchmarkRunner:
    """The standard suite wiring real subsystems (in-memory where noted)."""
    from ai_ecosystem.agent.executor import ParallelExecutor
    from ai_ecosystem.agent.multi import AgentDefinition, AgentManager, AgentRegistry
    from ai_ecosystem.agent.multi.messages import MessageBus
    from ai_ecosystem.agent.multi.messages import AgentMessage, MessageType
    from ai_ecosystem.agent.planner.validator import PlanValidator
    from ai_ecosystem.agent.verifier import Verifier
    from ai_ecosystem.cloud import (
        ComputeRequirements,
        ComputeRouter,
        MockSyncTransport,
        ProviderCapabilities,
        SyncManager,
        make_sync_object,
    )
    from ai_ecosystem.core.events import Event
    from ai_ecosystem.core.models import (
        Memory,
        Plan,
        PlanStep,
        Skill,
        Task,
        Tool,
        ToolResult,
    )
    from ai_ecosystem.core.models.enums import EventType, MemoryScope
    from ai_ecosystem.scheduler import GlobalScheduler
    from ai_ecosystem.scheduler import SqliteScheduledJobRepository
    from ai_ecosystem.skills import SkillRegistry
    from ai_ecosystem.core.persistence import (
        Database,
        SqliteMemoryRepository,
        SqliteSkillRepository,
        SqliteTaskRepository,
    )
    from ai_ecosystem.core.runtime import AgentRuntime
    from ai_ecosystem.interface import EventAdapter, WorkspaceManager
    from ai_ecosystem.interface import SqliteWorkspaceRepository
    from ai_ecosystem.personalization.memory import MemoryCandidate, MemoryStore
    from ai_ecosystem.tools import GrantAllAuthorizer, ToolRegistry, ToolRunner

    runner = BenchmarkRunner()

    def world(context: dict) -> dict:
        if "world" not in context:
            db = Database(":memory:")
            db.migrate()
            runtime = AgentRuntime(":memory:")
            registry = ToolRegistry()
            registry.register(
                Tool(name="work", input_schema={"required": []}),
                lambda args: ToolResult(success=True, output="ok"))
            tool_runner = ToolRunner(registry, GrantAllAuthorizer(), runtime.bus)
            agents = AgentRegistry(db)
            agents.register(AgentDefinition(id="bench-agent", name="bench",
                                            allowed_tools=["work"]))
            message_bus = MessageBus()
            message_bus.register("a")
            message_bus.register("b")
            skill_registry = SkillRegistry(SqliteSkillRepository(db))
            skill_registry.register(Skill(name="bench-skill", description="work helper",
                                          allowed_tools=["work"]))
            scheduler = GlobalScheduler(
                SqliteScheduledJobRepository(db),
                ComputeRouter({"local": ProviderCapabilities(
                    name="local", local=True)}))
            context["world"] = {"db": db, "runtime": runtime, "registry": registry,
                                "runner": tool_runner, "agents": agents,
                                "messages": message_bus, "skills": skill_registry,
                                "scheduler": scheduler}
        return context["world"]

    def plan() -> Plan:
        return Plan(goal="bench", steps=[PlanStep(
            id="s1", description="work", dependencies=[], tools=["work"],
            verification="v", completion_criteria="c")], final_verification="v")

    runner.add("task.create", "runtime", 50,
               lambda ctx, i: world(ctx)["runtime"].manager.create_task(f"t{i}", ""))
    runner.add("persistence.roundtrip", "runtime", 50,
               lambda ctx, i: SqliteTaskRepository(
                   world(ctx)["db"]).create(Task(title=f"t{i}")))
    runner.add("event.publish", "runtime", 200,
               lambda ctx, i: world(ctx)["runtime"].bus.publish(Event(
                   event_type=EventType.TASK_CREATED, task_id=f"t{i}")))
    runner.add("plan.validate", "planning", 100,
               lambda ctx, i: PlanValidator({"work"}).validate(plan()))
    runner.add("tool.execute", "execution", 50,
               lambda ctx, i: world(ctx)["runner"].run(
                   world(ctx)["registry"].build_call("t", "work", {})))
    runner.add("dag.execute", "execution", 20,
               lambda ctx, i: ParallelExecutor(
                   world(ctx)["runner"], world(ctx)["registry"]).execute(f"t{i}", plan()))
    runner.add("verify.command", "verification", 50,
               lambda ctx, i: Verifier().verify(
                   "t", "s", [ToolResult(success=True, output="ok")],
                   [{"strategy": "command_results"}]))
    runner.add("memory.insert", "memory", 50,
               lambda ctx, i: SqliteMemoryRepository(
                   world(ctx)["db"]).create(Memory(content=f"fact {i}")))
    runner.add("memory.retrieve", "memory", 20,
               lambda ctx, i: MemoryStore(
                   SqliteMemoryRepository(world(ctx)["db"])).retrieve(
                       MemoryScope.GLOBAL, query="fact"))
    runner.add("skill.lookup", "skills", 100,
               lambda ctx, i: world(ctx)["skills"].lookup("bench-skill"))
    runner.add("skill.discover", "skills", 50,
               lambda ctx, i: world(ctx)["skills"].select("work helper"))
    runner.add("agent.delegate", "agents", 30,
               lambda ctx, i: world(ctx)["agents"].lookup("bench-agent"))
    runner.add("message.delivery", "agents", 100,
               lambda ctx, i: world(ctx)["messages"].send(AgentMessage(
                   sender="a", recipient="b", task_id=f"t{i}",
                   message_type=MessageType.TASK_PROGRESS,
                   payload={"n": i}, correlation_id="bench")))
    runner.add("scheduler.tick", "scheduling", 20,
               lambda ctx, i: world(ctx)["scheduler"].tick())
    runner.add("provider.select", "cloud", 100,
               lambda ctx, i: ComputeRouter({
                   "local": ProviderCapabilities(name="local", local=True),
                   "oci": ProviderCapabilities(name="oci")}).route(
                       ComputeRequirements()))
    runner.add("sync.manifest", "cloud", 20,
               lambda ctx, i: SyncManager(transport=MockSyncTransport()).manifest(
                   [make_sync_object("task", f"t{i}", {"state": "x"})]))
    runner.add("workspace.update", "ui", 50,
               lambda ctx, i: WorkspaceManager(SqliteWorkspaceRepository(
                   world(ctx)["db"])).create(f"p{i}"))
    runner.add("viz.ingest", "ui", 100,
               lambda ctx, i: EventAdapter().ingest(Event(
                   event_type=EventType.TASK_CREATED, task_id=f"t{i}")))
    return runner
