"""Part 2: TaskManager + AgentRuntime behavior."""

import pytest

from ai_ecosystem.core.errors import InvalidStateTransitionError, ResourceNotFoundError
from ai_ecosystem.core.events import EventBus
from ai_ecosystem.core.models import Goal, TaskState
from ai_ecosystem.core.models.enums import EventType
from ai_ecosystem.core.persistence import (
    Database,
    SqliteExecutionContextRepository,
    SqliteTaskRepository,
)
from ai_ecosystem.core.runtime import AgentRuntime, TaskManager


@pytest.fixture()
def manager():
    db = Database(":memory:")
    db.migrate()
    bus = EventBus()
    yield TaskManager(SqliteTaskRepository(db), SqliteExecutionContextRepository(db), bus), bus
    db.close()


def test_create_task_persists_and_emits(manager):
    mgr, bus = manager
    seen = []
    bus.subscribe(EventType.TASK_CREATED, seen.append)
    task, ctx = mgr.create_task("inspect", "inspect repo")
    assert task.state == TaskState.CREATED
    assert ctx.task_id == task.id and ctx.goal == "inspect repo"
    assert mgr.get_task(task.id).id == task.id
    assert len(seen) == 1


def test_transition_persists_task_and_context(manager):
    mgr, _ = manager
    task, _ = mgr.create_task("t")
    updated = mgr.transition(task.id, TaskState.UNDERSTANDING)
    assert updated.state == TaskState.UNDERSTANDING
    assert mgr.get_context(task.id).current_state == TaskState.UNDERSTANDING


def test_transition_rejects_illegal_edge(manager):
    mgr, _ = manager
    task, _ = mgr.create_task("t")
    with pytest.raises(InvalidStateTransitionError):
        mgr.transition(task.id, TaskState.EXECUTING)


def test_transition_unknown_task(manager):
    mgr, _ = manager
    with pytest.raises(ResourceNotFoundError):
        mgr.transition("nope", TaskState.UNDERSTANDING)


def test_runtime_submit_goal_and_resume():
    runtime = AgentRuntime(":memory:")
    try:
        task, _ = runtime.submit_goal(Goal(title="Ship", success_criteria=["done"]))
        runtime.manager.transition(task.id, TaskState.UNDERSTANDING)
        resumed = runtime.manager.get_context(task.id)
    finally:
        runtime.shutdown()
    assert resumed.current_state == TaskState.UNDERSTANDING
    assert resumed.metadata["success_criteria"] == ["done"]
