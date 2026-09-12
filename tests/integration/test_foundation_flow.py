"""Parts 2-6 together: goal -> plan -> authorize -> execute -> persist.

Uses a mock model, the real policy engine, and real tools confined to a
tmp directory. Proves the planner cannot emit a plan the runtime cannot
validate, and that every execution leaves an audit trail.
"""

from ai_ecosystem.agent import DependencyResolver, ModelReasoningBackend
from ai_ecosystem.core.events import Event
from ai_ecosystem.core.models import Goal, TaskState
from ai_ecosystem.core.models.enums import EventType
from ai_ecosystem.core.persistence import DbEventStore
from ai_ecosystem.core.runtime import AgentRuntime
from ai_ecosystem.intelligence import MockModelProvider, ModelResponse
from ai_ecosystem.security import AuthorizationManager, RiskContext
from ai_ecosystem.tools import ToolRegistry, ToolRunner, filesystem_tools

LIFECYCLE = [
    TaskState.UNDERSTANDING,
    TaskState.AWARENESS,
    TaskState.RESEARCHING,
    TaskState.PLANNING,
    TaskState.WAITING_PERMISSION,
    TaskState.EXECUTING,
    TaskState.VERIFYING,
    TaskState.COMPLETED,
]

CANNED = {
    "goal": "Inventory workspace",
    "steps": [
        {
            "id": "s1",
            "description": "List workspace files",
            "dependencies": [],
            "tools": ["filesystem.list"],
            "risk": "LOW",
            "verification": "listing captured",
            "completion_criteria": "entries listed",
        },
        {
            "id": "s2",
            "description": "Read the notes file",
            "dependencies": ["s1"],
            "tools": ["filesystem.read"],
            "risk": "LOW",
            "verification": "content captured",
            "completion_criteria": "notes read",
        },
    ],
    "final_verification": "inventory complete",
}


def test_foundation_flow(tmp_path):
    db_path = str(tmp_path / "eco.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("inventory me")

    runtime = AgentRuntime(db_path)
    store = DbEventStore(runtime.events_repo)
    store.attach(runtime.bus)
    try:
        task, ctx = runtime.submit_goal(Goal(title="Inventory workspace"))
        for state in LIFECYCLE[:4]:
            runtime.manager.transition(task.id, state)

        backend = ModelReasoningBackend(
            MockModelProvider("mock", handler=lambda req: ModelResponse(structured=CANNED))
        )
        registry = ToolRegistry()
        for tool, handler in filesystem_tools(workspace):
            registry.register(tool, handler)
        known = [t.name for t in registry.list_tools()]
        plan = backend.plan("Inventory workspace", known)

        ordered = DependencyResolver.order(plan.steps)
        assert [s.id for s in ordered] == ["s1", "s2"]
        ctx.plan = plan
        runtime.contexts_repo.save(ctx)

        for state in LIFECYCLE[4:6]:
            runtime.manager.transition(task.id, state)

        authorizer = AuthorizationManager(
            registry, context=RiskContext(agent_id="mvp", root=str(workspace))
        )
        runner = ToolRunner(registry, authorizer, runtime.bus)
        arguments = {"s1": {"path": "."}, "s2": {"path": "notes.txt"}}
        for step in ordered:
            call = registry.build_call(task.id, step.tools[0], arguments[step.id])
            ctx.permissions.append(authorizer.authorize(task.id, registry.get(call.tool), call))
            result = runner.run(call)
            assert result.success, result.error
            ctx.tool_results.append(result)
        runtime.contexts_repo.save(ctx)

        for state in LIFECYCLE[6:]:
            runtime.manager.transition(task.id, state)
    finally:
        runtime.shutdown()

    # Restart: everything must have survived.
    reopened = AgentRuntime(db_path)
    try:
        resumed_task = reopened.manager.get_task(task.id)
        resumed_ctx = reopened.manager.get_context(task.id)
        events = DbEventStore(reopened.events_repo).list()
    finally:
        reopened.shutdown()

    assert resumed_task.state is TaskState.COMPLETED
    assert resumed_ctx.plan is not None and len(resumed_ctx.plan.steps) == 2
    assert len(resumed_ctx.tool_results) == 2
    assert len(resumed_ctx.permissions) == 2  # full audit trail
    kinds = [e.event_type for e in events]
    assert kinds[0] is EventType.TASK_CREATED
    assert kinds.count(EventType.TOOL_COMPLETED) == 2
    assert isinstance(events[0], Event)
