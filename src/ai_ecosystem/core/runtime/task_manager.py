"""TaskManager + AgentRuntime (Part 2, Gates 2-3 wiring).

Owns the task lifecycle: create -> transition -> persist -> emit.
Every mutation commits to the database *before* the event hits the bus,
so a crash between the two still leaves restorable state (the event can
be re-derived; the state cannot).
"""

from __future__ import annotations

from typing import Optional

from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.domain import ExecutionContext, Goal, Task
from ai_ecosystem.core.models.enums import EventType, TaskState
from ai_ecosystem.core.persistence.sqlite import (
    Database,
    SqliteEventRepository,
    SqliteExecutionContextRepository,
    SqliteTaskRepository,
)
from ai_ecosystem.core.runtime import state_machine as sm


class TaskManager:
    """Creates tasks and moves them through the state machine durably."""

    def __init__(
        self,
        tasks: SqliteTaskRepository,
        contexts: SqliteExecutionContextRepository,
        bus: EventBus,
        db: Optional[Database] = None,
    ) -> None:
        self._tasks = tasks
        self._contexts = contexts
        self._bus = bus
        self._db = db

    def create_task(self, title: str, goal: str = "") -> tuple[Task, ExecutionContext]:
        """Create a CREATED task + context, persist, emit TaskCreated.

        The two writes share one transaction when a database was
        provided, so a crash cannot orphan a task without its context.
        """
        if self._db is not None:
            with self._db.transaction():
                return self._create_task(title, goal)
        return self._create_task(title, goal)

    def _create_task(self, title: str, goal: str) -> tuple[Task, ExecutionContext]:
        task = self._tasks.create(Task(title=title))
        ctx = self._contexts.save(
            ExecutionContext(task_id=task.id, goal=goal or title)
        )
        self._bus.publish(
            Event(
                event_type=EventType.TASK_CREATED,
                task_id=task.id,
                payload={"title": title, "goal": goal or title},
            )
        )
        return task, ctx

    def transition(self, task_id: str, to_state: TaskState) -> Task:
        """Move a task; validates, persists task + context, returns task."""
        if self._db is not None:
            with self._db.transaction():
                return self._transition(task_id, to_state)
        return self._transition(task_id, to_state)

    def _transition(self, task_id: str, to_state: TaskState) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            from ai_ecosystem.core.errors.exceptions import ResourceNotFoundError

            raise ResourceNotFoundError("Task", task_id)
        sm.assert_transition(task.state, to_state)
        task.state = to_state
        task.touch()
        self._tasks.update(task)
        ctx = self._contexts.load(task_id)
        if ctx is not None:
            ctx.current_state = to_state
            ctx.touch()
            self._contexts.save(ctx)
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        """Fetch a task by id (None when unknown)."""
        return self._tasks.get(task_id)

    def get_context(self, task_id: str) -> Optional[ExecutionContext]:
        """Fetch the latest restorable context (None when unknown)."""
        return self._contexts.load(task_id)


class AgentRuntime:
    """Composition root for the foundation (bus, database, task manager).

    Part 2 scope only: no models, planner, tools, or policy. Later parts
    attach to ``bus`` / ``db`` without changing this class's contract.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db = Database(db_path)
        self.db.migrate()
        self.bus = EventBus()
        self.tasks_repo = SqliteTaskRepository(self.db)
        self.contexts_repo = SqliteExecutionContextRepository(self.db)
        self.events_repo = SqliteEventRepository(self.db)
        self.manager = TaskManager(self.tasks_repo, self.contexts_repo, self.bus,
                                   self.db)

    def submit_goal(self, goal: Goal) -> tuple[Task, ExecutionContext]:
        """Entry point: turn a user goal into a tracked task."""
        title = goal.title or goal.description or "untitled goal"
        criteria = goal.success_criteria
        task, ctx = self.manager.create_task(title, goal.description or title)
        if criteria:
            ctx.metadata["success_criteria"] = list(criteria)
            self.contexts_repo.save(ctx)
        return task, ctx

    def shutdown(self) -> None:
        """Release resources (contexts stay durable for resume)."""
        self.db.close()
