"""Gate 3: repository CRUD, transactions, migrations, recovery."""

import threading

import pytest

from ai_ecosystem.core.models import ExecutionContext, Memory, Plan, Skill, Task
from ai_ecosystem.core.persistence import (
    Database,
    SqliteExecutionContextRepository,
    SqliteMemoryRepository,
    SqlitePlanRepository,
    SqliteSkillRepository,
    SqliteTaskRepository,
)


@pytest.fixture()
def db():
    database = Database(":memory:")
    database.migrate()
    yield database
    database.close()


def test_migrate_is_idempotent_and_versioned(db):
    assert db.migrate() == 1
    assert db.schema_version() == 1


def test_task_crud(db):
    repo = SqliteTaskRepository(db)
    task = repo.create(Task(title="t"))
    assert repo.get(task.id).title == "t"
    task.title = "t2"
    assert repo.update(task).title == "t2"
    assert len(repo.list()) == 1
    assert repo.delete(task.id) is True
    assert repo.get(task.id) is None
    assert repo.delete(task.id) is False


def test_plan_memory_skill_round_trips(db):
    plan = SqlitePlanRepository(db).create(Plan(goal="g"))
    assert SqlitePlanRepository(db).get(plan.id).goal == "g"
    mem = SqliteMemoryRepository(db).create(Memory(content="prefers concise"))
    assert SqliteMemoryRepository(db).get(mem.id).content == "prefers concise"
    skill = SqliteSkillRepository(db).create(Skill(name="repo-scan"))
    assert SqliteSkillRepository(db).get(skill.id).name == "repo-scan"


def test_transaction_rollback(db):
    repo = SqliteTaskRepository(db)
    with pytest.raises(RuntimeError):
        with db.transaction():
            repo.create(Task(title="doomed"))
            raise RuntimeError("boom")
    assert repo.list() == []


def test_transaction_commit(db):
    repo = SqliteTaskRepository(db)
    with db.transaction():
        repo.create(Task(title="kept"))
    assert len(repo.list()) == 1


def test_concurrent_access(db):
    repo = SqliteTaskRepository(db)
    threads = [
        threading.Thread(target=lambda: repo.create(Task(title=f"t{i}")))
        for i in range(20)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(repo.list()) == 20


def test_concurrent_mixed_read_write_isolation(db):
    """Threads must never read each other's rows (cursor discipline)."""
    from ai_ecosystem.core.persistence import SqliteExecutionContextRepository

    tasks = SqliteTaskRepository(db)
    contexts = SqliteExecutionContextRepository(db)
    errors: list = []

    def worker(index: int) -> None:
        try:
            for round_ in range(25):
                task = tasks.create(Task(title=f"w{index}-r{round_}"))
                fetched = tasks.get(task.id)
                assert fetched is not None and fetched.title == f"w{index}-r{round_}"
                contexts.save(ExecutionContext(task_id=task.id, goal=f"g{index}"))
                loaded = contexts.load(task.id)
                assert loaded is not None and loaded.goal == f"g{index}"
                tasks.list()
        except Exception as exc:  # noqa: BLE001 -- collected, then raised
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len(tasks.list()) == 200


def test_context_save_load_delete(db):
    repo = SqliteExecutionContextRepository(db)
    repo.save(ExecutionContext(task_id="t1", goal="g"))
    assert repo.load("t1").goal == "g"
    assert repo.load("unknown") is None
    assert repo.delete("t1") is True
    assert repo.load("t1") is None


def test_restart_recovery(tmp_path):
    path = str(tmp_path / "eco.db")
    first = Database(path)
    first.migrate()
    SqliteTaskRepository(first).create(Task(id="task-9", title="survive"))
    SqliteExecutionContextRepository(first).save(
        ExecutionContext(task_id="task-9", goal="survive", current_step="step-2")
    )
    first.close()  # simulate kill: no shutdown ceremony, just close

    second = Database(path)  # restart
    second.migrate()
    try:
        task = SqliteTaskRepository(second).get("task-9")
        ctx = SqliteExecutionContextRepository(second).load("task-9")
    finally:
        second.close()
    assert task is not None and task.title == "survive"
    assert ctx is not None and ctx.current_step == "step-2"
