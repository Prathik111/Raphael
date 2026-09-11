"""Gate 39: durable priority scheduler over the compute router."""

import time
from datetime import timedelta

import pytest

from ai_ecosystem.cloud import (
    ComputePolicy,
    ComputeRequirements,
    ComputeRouter,
    ProviderCapabilities,
)
from ai_ecosystem.core.errors import DomainValidationError, ResourceNotFoundError
from ai_ecosystem.core.models.base import utcnow
from ai_ecosystem.core.persistence import Database
from ai_ecosystem.scheduler import (
    GlobalScheduler,
    ScheduledJob,
    ScheduledStatus,
    SqliteScheduledJobRepository,
)


def _router():
    return ComputeRouter(
        {
            "local": ProviderCapabilities(
                name="local",
                local=True,
                ram_gb=16.0,
                cost_per_hour=0.0,
                latency_class="fast",
            ),
            "oci": ProviderCapabilities(
                name="oci", ram_gb=64.0, cost_per_hour=2.0, latency_class="standard"
            ),
        }
    )


@pytest.fixture()
def setup():
    db = Database(":memory:")
    db.migrate()
    repo = SqliteScheduledJobRepository(db)
    calls = []
    scheduler = GlobalScheduler(
        repo,
        _router(),
        dispatch=lambda job, target: calls.append((job.id, target.provider)) or "done",
    )
    yield scheduler, repo, calls
    db.close()


def _job(goal="g", **kw):
    args = {"goal": goal, "requirements": ComputeRequirements(ram_gb=4.0)}
    args.update(kw)
    return ScheduledJob(**args)


def test_priority(setup):
    scheduler, _, _ = setup
    scheduler.submit(_job("low", priority=1))
    scheduler.submit(_job("high", priority=10))
    ran = scheduler.tick()
    assert [job.goal for job in ran] == ["high", "low"]


def test_dependencies(setup):
    scheduler, _, _ = setup
    first = scheduler.submit(_job("first"))
    scheduler.submit(_job("second", dependencies=[first.id]))
    ran = scheduler.tick()
    assert [job.goal for job in ran] == ["first"]
    ran = scheduler.tick()
    assert [job.goal for job in ran] == ["second"]


def test_deadlines(setup):
    scheduler, _, _ = setup
    scheduler.submit(_job("late", deadline=utcnow() - timedelta(seconds=1)))
    scheduler.submit(_job("fine", deadline=utcnow() + timedelta(hours=1)))
    ran = scheduler.tick()
    assert [job.goal for job in ran] == ["fine"]
    assert scheduler._repo.list()[0].status is ScheduledStatus.FAILED


def test_local_cloud_routing(setup):
    scheduler, _, _ = setup
    scheduler.submit(_job("cheap", requirements=ComputeRequirements(ram_gb=4.0)))
    ran = scheduler.tick()
    assert ran[0].routed_provider == "local"


def test_retry(setup):
    scheduler, _, _ = setup
    attempts = {"n": 0}

    def flaky(job, target):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient dispatch failure")
        return "recovered"

    scheduler._dispatch = flaky
    scheduler.submit(_job("flaky", max_attempts=3))
    assert scheduler.tick()[0].status is ScheduledStatus.QUEUED  # requeued
    ran = scheduler.tick()
    assert ran[0].status is ScheduledStatus.SUCCEEDED
    assert attempts["n"] == 2


def test_cancellation(setup):
    scheduler, _, _ = setup
    job = scheduler.submit(_job("cancel me"))
    cancelled = scheduler.cancel(job.id)
    assert cancelled.status is ScheduledStatus.CANCELLED
    assert scheduler.tick() == []
    with pytest.raises(DomainValidationError):
        scheduler.cancel(job.id)
    with pytest.raises(ResourceNotFoundError):
        scheduler.cancel("ghost")


def test_restart(setup):
    scheduler, repo, _ = setup
    job = scheduler.submit(_job("resume me"))
    stored = repo.get(job.id)
    stored.status = ScheduledStatus.RUNNING  # simulate kill mid-run
    repo.update(stored)
    reset = scheduler.resume_interrupted()
    assert [job.id for job in reset] == [stored.id]
    assert scheduler.tick()[0].status is ScheduledStatus.SUCCEEDED


def test_duplicate_prevention(setup):
    scheduler, _, _ = setup
    first = scheduler.submit(_job("once", idempotency_key="k1"))
    second = scheduler.submit(_job("once", idempotency_key="k1"))
    assert first.id == second.id
    assert len(scheduler._repo.list()) == 1


def test_fairness_fifo_for_equal_priority(setup):
    scheduler, _, _ = setup
    for index in range(3):
        scheduler.submit(_job(f"job-{index}"))
    ran = scheduler.tick()
    assert [job.goal for job in ran] == ["job-0", "job-1", "job-2"]


def test_policy_rejection(setup):
    router = ComputeRouter(
        {"local": ProviderCapabilities(name="local", local=True, ram_gb=1.0)},
        policy=ComputePolicy(blocked_providers=["local"]),
    )
    scheduler = GlobalScheduler(setup[1], router, dispatch=lambda job, target: "never")
    scheduler.submit(_job("blocked", requirements=ComputeRequirements(ram_gb=4.0)))
    ran = scheduler.tick()
    assert ran[0].status is ScheduledStatus.FAILED
    assert "routing failed" in ran[0].result_summary


def test_scheduler_audit_hook(setup):
    notes = []
    scheduler, _, _ = setup
    scheduler._audit = notes.append
    scheduler.submit(_job("audited"))
    scheduler.tick()
    assert any(n["action"] == "scheduler.dispatch" for n in notes)


def test_scheduler_perf_smoke(setup):
    scheduler, _, _ = setup
    for index in range(100):
        scheduler.submit(_job(f"job-{index}"))
    started = time.monotonic()
    ran = scheduler.tick()
    elapsed = time.monotonic() - started
    print(f"\nscheduler smoke: 100 jobs routed+run in {elapsed:.2f}s")
    assert len(ran) == 100
    assert elapsed < 15
