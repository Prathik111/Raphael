"""Final E2E (Gates 15-19): analyze + research + parallel agents + memory.

Goal: "Analyze a project, research one question, perform two
independent tasks, combine their results, and remember the workflow."

Deterministic throughout: mock probe, mock search, mock tools, real
policy/engine/bus/persistence. Verifies decomposition, assignment,
permissions, parallelism, routing, aggregation, verification, denial
of the unauthorized action, scoped memory, policy-gated observation,
proposal-without-policy-change, event trace, and restart safety.
"""

import time

from ai_ecosystem.agent.multi import (
    AgentDefinition,
    AgentManager,
    AgentRegistry,
    Coordinator,
    MessageBus,
    SubtaskSpec,
)
from ai_ecosystem.agent.verifier import Verifier
from ai_ecosystem.core.models import Plan, PlanStep, Tool, ToolResult
from ai_ecosystem.core.models.enums import (
    EventType,
    MemoryScope,
    MemoryType,
    RiskLevel,
)
from ai_ecosystem.core.persistence import (
    Database,
    SqliteMemoryRepository,
    SqliteMessageRepository,
    SqliteUsageEventRepository,
)
from ai_ecosystem.core.runtime import AgentRuntime
from ai_ecosystem.intelligence.research.manager import ResearchManager
from ai_ecosystem.intelligence.research.models import ResearchQuery
from ai_ecosystem.learning.models import ObservationMode, ObservationPolicy
from ai_ecosystem.learning.observer import (
    PatternDetector,
    ProposalEngine,
    UsageObserver,
)
from ai_ecosystem.personalization.memory import MemoryCandidate, MemoryStore
from ai_ecosystem.personalization.personality import (
    PersonalizationEngine,
    PersonalityStore,
    PreferenceStore,
)
from ai_ecosystem.system.monitor import SystemAwarenessManager
from ai_ecosystem.system.monitor.probe import MockProbe
from ai_ecosystem.system.monitor.models import (
    Capabilities,
    CpuInfo,
    MemoryInfo,
    PressureLevel,
    SystemSnapshot,
)
from ai_ecosystem.tools import ToolRegistry


def _ok(output="ok"):
    def run(args):
        return ToolResult(success=True, output=output)

    return run


def _step(sid, tool, deps=()):
    return PlanStep(
        id=sid,
        description=sid,
        dependencies=list(deps),
        tools=[tool],
        verification="v",
        completion_criteria="c",
    )


def test_multi_agent_end_to_end(tmp_path):
    wall_started = time.monotonic()
    (tmp_path / "project").mkdir()
    (tmp_path / "project" / "main.py").write_text("print('hi')\n")
    path = str(tmp_path / "e2e.db")
    db = Database(path)
    db.migrate()
    runtime = AgentRuntime(path)
    bus = runtime.bus
    events: list = []
    bus.subscribe_all(events.append)

    # -- tools (incl. one forbidden tool for the denial probe) --
    calls = {"danger": 0}

    def danger(args):
        calls["danger"] += 1
        return ToolResult(success=True, output="pwned")

    registry = ToolRegistry()
    registry.register(
        Tool(name="filesystem.read", input_schema={"required": []}, risk_level=RiskLevel.LOW),
        lambda args: ToolResult(success=True, output="print('hi')"),
    )
    registry.register(
        Tool(name="web.search", input_schema={"required": ["query"]}, risk_level=RiskLevel.LOW),
        lambda args: ToolResult(
            success=True,
            output={
                "sources": [
                    {
                        "title": "Guide",
                        "origin": "docs",
                        "url": "https://docs.test/g",
                        "claims": ["Small projects benefit from a single entry point."],
                    }
                ]
            },
        ),
    )
    registry.register(
        Tool(name="analyze", input_schema={"required": []}, risk_level=RiskLevel.LOW),
        _ok("analysis done"),
    )
    registry.register(
        Tool(name="report", input_schema={"required": []}, risk_level=RiskLevel.LOW),
        _ok("report written"),
    )
    registry.register(
        Tool(name="danger", input_schema={"required": []}, risk_level=RiskLevel.HIGH), danger
    )

    # -- understanding + awareness ( Gates 14/15 inputs to decomposition) --
    snapshot = SystemSnapshot(
        operating_system="TestOS",
        architecture="x86_64",
        cpu=CpuInfo(model="t", logical_processors=8, utilization_percent=20.0),
        memory=MemoryInfo(
            total_bytes=8_000_000_000,
            available_bytes=6_000_000_000,
            used_bytes=2_000_000_000,
            utilization_percent=25.0,
        ),
        capabilities=Capabilities(python_available=True, storage_available=True),
        pressure=PressureLevel.LOW,
    )
    awareness = SystemAwarenessManager(MockProbe(snapshot), bus=bus)
    context = awareness.context()
    assert context.can_compute_locally is True

    # -- research one question --
    from ai_ecosystem.tools import ToolRunner
    from ai_ecosystem.security import AuthorizationManager

    authorizer = AuthorizationManager(registry)
    runner = ToolRunner(registry, authorizer, bus)
    research = ResearchManager(runner, registry, bus=bus).research(
        ResearchQuery(query="project entry points")
    )
    assert len(research.evidence) == 1

    # -- supervisor decomposes across three specialists --
    agents = AgentRegistry(db, bus)
    agents.register(
        AgentDefinition(
            id="researcher",
            name="researcher",
            role="research",
            capabilities=["research"],
            allowed_tools=["web.search", "filesystem.read"],
        )
    )
    agents.register(
        AgentDefinition(
            id="analyst",
            name="analyst",
            role="analysis",
            capabilities=["analysis"],
            allowed_tools=["filesystem.read", "analyze"],
        )
    )
    agents.register(
        AgentDefinition(
            id="coder",
            name="coder",
            role="coding",
            capabilities=["coding"],
            allowed_tools=["report"],
        )
    )
    manager = AgentManager(agents, runtime, registry, max_workers=3, bus=bus)
    for agent_id in ("researcher", "analyst", "coder"):
        manager.spawn(agent_id)
    messages = MessageBus(bus, repository=SqliteMessageRepository(db))
    for agent_id in ("researcher", "analyst", "coder", "supervisor"):
        messages.register(agent_id)
    coordinator = Coordinator(manager, messages, verifier=Verifier(), bus=bus)

    research_plan = Plan(goal="research", steps=[_step("s1", "web.search")], final_verification="v")
    analysis_plan = Plan(
        goal="analyze",
        steps=[_step("s1", "filesystem.read"), _step("s2", "analyze", ["s1"])],
        final_verification="v",
    )
    coding_plan = Plan(goal="report", steps=[_step("s1", "report")], final_verification="v")
    outcome = coordinator.fan_out(
        "supervisor",
        [
            SubtaskSpec(
                "researcher",
                "research entry points",
                research_plan,
                {"s1": {"query": "entry points"}},
            ),
            SubtaskSpec("analyst", "analyze project", analysis_plan, {"s1": {"path": "main.py"}}),
            SubtaskSpec("coder", "write report", coding_plan),
        ],
        "corr-e2e",
    )

    # -- decomposition / assignment / parallelism / aggregation --
    assert set(outcome.results) == {"researcher", "analyst", "coder"}
    assert set(outcome.succeeded) == {"researcher", "analyst", "coder"}
    assert all(outcome.verified.values())  # every child result verified

    # -- handoff: research hands findings to the coder as data --
    # (coder already holds its TASK_REQUEST, so the handoff is second)
    messages.handoff("researcher", "coder", "t-e2e", "findings: single entry point", "corr-e2e")
    assert messages.pending("coder") == 2

    # -- unauthorized action is rejected (coder has no danger tool) --
    coder_runner, _ = manager.scoped_runner("coder")
    denied = coder_runner.run(registry.build_call("t", "danger", {}))
    assert denied.success is False
    assert calls["danger"] == 0

    # -- memory only for meaningful information --
    memories = MemoryStore(SqliteMemoryRepository(db), bus=bus, database=db)
    memory = memories.store(
        MemoryCandidate(
            content="Analyzed project uses a single entry point.",
            source="e2e",
            type=MemoryType.PROJECT,
            confidence=0.9,
            importance=0.8,
            scope=MemoryScope.PROJECT,
            scope_id="demo",
            reason="useful workflow observed",
        )
    )
    assert memory.scope is MemoryScope.PROJECT

    # -- usage observation follows policy; learning proposes, never imposes --
    observer = UsageObserver(
        ObservationPolicy(mode=ObservationMode.LOCAL_PERSISTENCE), SqliteUsageEventRepository(db)
    )
    for tool_name in ("web.search", "filesystem.read", "analyze", "report"):
        observer.observe_tool(tool_name, True, duration_ms=5.0, project="demo")
    patterns = PatternDetector(min_evidence=1).detect(observer.session_events())
    assert patterns
    policy_before = AuthorizationManager(registry).policy.model_dump()
    proposal = ProposalEngine(bus).propose(patterns[0])
    policy_after = AuthorizationManager(registry).policy.model_dump()
    assert policy_before == policy_after  # proposals cannot move policy
    assert proposal.status.value == "PROPOSED"  # and are not auto-applied

    # -- personalization still works at the end of the chain --
    personalization = PersonalizationEngine(
        PersonalityStore(db), PreferenceStore(db), memories, bus
    )
    personal_ctx = personalization.build_context(project_id="demo")
    assert personal_ctx.project_id == "demo"

    # -- event trace + restart safety --
    kinds = [e.event_type for e in events]
    for expected in (
        EventType.SYSTEM_SNAPSHOT_CREATED,
        EventType.RESEARCH_COMPLETED,
        EventType.AGENT_STARTED,
        EventType.AGENT_MESSAGE_SENT,
        EventType.TOOL_COMPLETED,
        EventType.AGENT_COMPLETED,
        EventType.MEMORY_CREATED,
        EventType.LEARNING_PROPOSAL_CREATED,
    ):
        assert expected in kinds, expected
    runtime.shutdown()
    db.close()
    wall = time.monotonic() - wall_started
    print(f"\ne2e multi-agent flow in {wall:.2f}s")

    reopened_db = Database(path)
    reopened_db.migrate()
    reopened = AgentRuntime(path)
    try:

        def count(table: str) -> int:
            return reopened_db.query(f"SELECT COUNT(*) FROM {table}")[0][0]

        assert count("agent_definitions") == 3
        assert count("agent_tasks") == 3
        assert count("agent_messages") >= 6
        assert count("memories") == 1
        assert count("usage_events") == 4
    finally:
        reopened.shutdown()
        reopened_db.close()
