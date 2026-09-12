"""Cross-gate E2E (Gates 20-24): desktop -> runtime -> agents -> UI -> OCI.

Deterministic throughout: local HTTP server, mock probe/search/tools/
transport, real policy/engine/bus/persistence. A test-local dispatch
function plays the runtime worker (explicitly E2E glue, not a new
orchestrator): every stage uses production components.
"""

import threading
import time

from ai_ecosystem.agent.multi import (
    AgentDefinition,
    AgentManager,
    AgentRegistry,
    MessageBus,
    SubtaskSpec,
    Supervisor,
)
from ai_ecosystem.agent.verifier import Verifier
from ai_ecosystem.cloud import MockSyncTransport, SyncManager, make_sync_object
from ai_ecosystem.core.events import EventBus, InMemoryEventStore
from ai_ecosystem.core.models import Plan, PlanStep, Tool, ToolResult
from ai_ecosystem.core.models.enums import (
    MemoryScope,
    RiskLevel,
    TaskState,
)
from ai_ecosystem.core.persistence import (
    Database,
    SqliteMemoryRepository,
    SqliteMessageRepository,
)
from ai_ecosystem.core.runtime import AgentRuntime
from ai_ecosystem.interface import (
    ApiClient,
    EventAdapter,
    LocalHttpServer,
    RuntimeAPI,
    SqliteWorkspaceRepository,
    WorkspaceManager,
)
from ai_ecosystem.intelligence.research.manager import ResearchManager
from ai_ecosystem.intelligence.research.models import ResearchQuery
from ai_ecosystem.personalization.memory import MemoryCandidate, MemoryStore
from ai_ecosystem.security import AuthorizationManager, RiskContext
from ai_ecosystem.system.monitor import SystemAwarenessManager
from ai_ecosystem.system.monitor.models import (
    Capabilities,
    CpuInfo,
    MemoryInfo,
    PressureLevel,
    SystemSnapshot,
)
from ai_ecosystem.system.monitor.probe import MockProbe
from ai_ecosystem.tools import ToolRegistry, ToolRunner


def _ok(output="ok"):
    def run(args):
        return ToolResult(success=True, output=output)

    return run


def test_desktop_to_cloud_end_to_end(tmp_path):
    wall_started = time.monotonic()
    path = str(tmp_path / "e2e.db")
    db = Database(path)
    db.migrate()
    runtime = AgentRuntime(path)
    bus = runtime.bus
    events = InMemoryEventStore()
    events.attach(bus)

    registry = ToolRegistry()
    registry.register(
        Tool(name="filesystem.read", input_schema={"required": []}, risk_level=RiskLevel.LOW),
        _ok("data"),
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
                        "claims": ["Entry points simplify small projects."],
                    }
                ]
            },
        ),
    )
    registry.register(
        Tool(name="analyze", input_schema={"required": []}, risk_level=RiskLevel.LOW),
        _ok("analysis"),
    )
    authorizer = AuthorizationManager(
        registry, context=RiskContext(agent_id="e2e", root=str(tmp_path))
    )
    runner = ToolRunner(registry, authorizer, bus)

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
    manager = AgentManager(agents, runtime, registry, max_workers=2, bus=bus)
    manager.spawn("researcher")
    manager.spawn("analyst")
    messages = MessageBus(bus, repository=SqliteMessageRepository(db))
    for agent_id in ("researcher", "analyst", "supervisor"):
        messages.register(agent_id)
    workspaces = WorkspaceManager(SqliteWorkspaceRepository(db), bus)
    workspace = workspaces.create("demo")
    memories = MemoryStore(SqliteMemoryRepository(db), bus=bus, database=db)
    transport = MockSyncTransport()
    sync = SyncManager(transport=transport, bus=bus)

    def dispatch(task_id):
        def work():
            mgr = runtime.manager
            mgr.transition(task_id, TaskState.UNDERSTANDING)
            awareness = SystemAwarenessManager(
                MockProbe(
                    SystemSnapshot(
                        operating_system="TestOS",
                        architecture="x86_64",
                        cpu=CpuInfo(model="t", logical_processors=4, utilization_percent=20.0),
                        memory=MemoryInfo(
                            total_bytes=8_000_000_000,
                            available_bytes=6_000_000_000,
                            used_bytes=2_000_000_000,
                            utilization_percent=25.0,
                        ),
                        capabilities=Capabilities(python_available=True, storage_available=True),
                        pressure=PressureLevel.LOW,
                    )
                ),
                bus=bus,
            )
            awareness.context()
            mgr.transition(task_id, TaskState.AWARENESS)
            research = ResearchManager(runner, registry, bus=bus).research(
                ResearchQuery(query="entry points")
            )
            mgr.transition(task_id, TaskState.RESEARCHING)
            supervisor = Supervisor(manager, verifier=Verifier())
            outcome = supervisor.run_goal(
                "analyze",
                [
                    SubtaskSpec(
                        "researcher",
                        "research",
                        Plan(
                            goal="r",
                            steps=[
                                PlanStep(
                                    id="s1",
                                    description="search",
                                    dependencies=[],
                                    tools=["web.search"],
                                    verification="v",
                                    completion_criteria="c",
                                )
                            ],
                            final_verification="v",
                        ),
                        {"s1": {"query": "entry points"}},
                    ),
                    SubtaskSpec(
                        "analyst",
                        "analyze",
                        Plan(
                            goal="a",
                            steps=[
                                PlanStep(
                                    id="s1",
                                    description="read",
                                    dependencies=[],
                                    tools=["filesystem.read"],
                                    verification="v",
                                    completion_criteria="c",
                                ),
                                PlanStep(
                                    id="s2",
                                    description="analyze",
                                    dependencies=["s1"],
                                    tools=["analyze"],
                                    verification="v",
                                    completion_criteria="c",
                                ),
                            ],
                            final_verification="v",
                        ),
                        {"s1": {"path": "main.py"}},
                    ),
                ],
            )
            assert len(outcome.succeeded) == 2
            messages.handoff("researcher", "analyst", task_id, "findings: entry points", "corr-e2e")
            mgr.transition(task_id, TaskState.PLANNING)
            mgr.transition(task_id, TaskState.WAITING_PERMISSION)
            mgr.transition(task_id, TaskState.EXECUTING)
            mgr.transition(task_id, TaskState.VERIFYING)
            workspaces.apply_update(
                workspace.id,
                {
                    "type": "prose",
                    "props": {"title": "Research panel", "body": research.evidence[0].claim},
                    "data_ref": {"kind": "research", "ref_id": "r1"},
                },
            )
            memories.store(
                MemoryCandidate(
                    content="Entry points simplify small projects.",
                    source="e2e",
                    confidence=0.9,
                    importance=0.8,
                    scope=MemoryScope.PROJECT,
                    scope_id="demo",
                    reason="useful finding",
                )
            )
            sync.sync([make_sync_object("task", task_id, {"state": "COMPLETED"})])
            mgr.transition(task_id, TaskState.COMPLETED)

        threading.Thread(target=work, daemon=True).start()

    api = RuntimeAPI(runtime, dispatch=dispatch, event_store=events)
    server = LocalHttpServer(api).start()
    try:
        client = ApiClient(server.url)
        assert client.health() == {"status": "ok"}
        created = client.submit("Research X and analyze the project.")
        task_id = created["task_id"]
        deadline = time.monotonic() + 30
        state = ""
        while time.monotonic() < deadline:
            state = client.get(f"/tasks/{task_id}")["state"]
            if state == "COMPLETED":
                break
            time.sleep(0.05)
        assert state == "COMPLETED"

        # UI polling sees the parent trail; the full trail spans child tasks.
        seen = client.get(f"/tasks/{task_id}/events")
        parent_kinds = [e["type"] for e in seen]
        assert "TaskCreated" in parent_kinds
        kinds = [e.event_type.value for e in events.list()]
        for expected in (
            "SystemSnapshotCreated",
            "ResearchCompleted",
            "AgentStarted",
            "AgentCompleted",
            "ToolCompleted",
            "WorkspaceUpdated",
            "MemoryCreated",
            "SyncObjectUploaded",
            "SyncCompleted",
        ):
            assert expected in kinds, expected

        # Visualization derives the same story without touching the runtime.
        adapter = EventAdapter()
        adapter.ingest_store(_bus_events(bus, events, task_id))
        view = adapter.snapshot(task_id)
        assert view is not None
        assert view.verification in ("PASSED", "PENDING")

        # OCI outage: local runtime is unaffected; sync fails recoverably.
        transport.down = True
        outage = sync.sync([make_sync_object("task", task_id, {"state": "x"})])
        assert outage.state.value == "FAILED"
        assert client.get(f"/tasks/{task_id}")["state"] == "COMPLETED"
        transport.down = False
        recovered = sync.retry_sync([make_sync_object("task", task_id, {"state": "x"})])
        assert recovered.state.value == "COMPLETED"
    finally:
        server.stop()
        runtime.shutdown()
        db.close()

    # Restart: tasks, workspace, messages, and memory survive.
    reopened_db = Database(path)
    reopened_db.migrate()
    reopened = AgentRuntime(path)
    try:
        assert reopened.manager.get_task(task_id) is not None
        assert len(SqliteWorkspaceRepository(reopened_db).get(workspace.id).nodes) == 1
        assert reopened_db.query("SELECT COUNT(*) FROM agent_messages")[0][0] >= 1
        assert reopened_db.query("SELECT COUNT(*) FROM memories")[0][0] == 1
    finally:
        reopened.shutdown()
        reopened_db.close()
    print(f"\ndesktop->cloud e2e in {time.monotonic() - wall_started:.2f}s")


def _bus_events(bus: EventBus, store: InMemoryEventStore, task_id: str):
    """Replay helper: ordered store events for one task."""
    return [e for e in store.list() if e.task_id == task_id]
