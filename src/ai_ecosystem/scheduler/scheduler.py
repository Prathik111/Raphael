"""Ecosystem-wide scheduler: durable ordering, dependency safety, and explicit dispatch."""

from __future__ import annotations

import time
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional

from pydantic import Field

from ai_ecosystem.cloud.routing import ComputeRequirements, ComputeRouter, ComputeTarget, RoutingError
from ai_ecosystem.core.errors.exceptions import DomainValidationError
from ai_ecosystem.core.models.base import Entity, utcnow


class ScheduledStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ScheduledJob(Entity):
    goal: str = ""
    requirements: ComputeRequirements = Field(default_factory=ComputeRequirements)
    priority: int = 0
    deadline: Optional[datetime] = None
    dependencies: list[str] = Field(default_factory=list)
    idempotency_key: str = ""
    agent_id: str = ""
    provider_hint: str = ""
    status: ScheduledStatus = ScheduledStatus.QUEUED
    attempts: int = 0
    max_attempts: int = 3
    routed_provider: str = ""
    result_summary: str = ""
    created_at: datetime = Field(default_factory=utcnow)


DispatchFn = Callable[[ScheduledJob, ComputeTarget], str]


class GlobalScheduler:
    """Persistent priority scheduler with bounded retries and explicit dispatch."""

    def __init__(
        self,
        repository: Any,
        router: ComputeRouter,
        dispatch: Optional[DispatchFn] = None,
        audit: Optional[Callable[[dict], None]] = None,
        clock: Optional[Callable[[], float]] = None,
        quiet_hours: Optional[tuple[int, int]] = None,
    ) -> None:
        import threading
        self._repo = repository
        self._router = router
        self._dispatch = dispatch
        self._audit = audit
        self._clock = clock or time.time
        self._quiet_hours = quiet_hours
        self._submit_lock = threading.Lock()

    def submit(self, job: ScheduledJob) -> ScheduledJob:
        """Queue a job after validating ids, attempts, dependencies and cycles."""
        if job.max_attempts < 1:
            raise DomainValidationError("max_attempts must be >= 1")
        if job.id in job.dependencies:
            raise DomainValidationError("a job cannot depend on itself")
        with self._submit_lock:
            existing_jobs = self._repo.list()
            if job.idempotency_key:
                for existing in existing_jobs:
                    if existing.idempotency_key == job.idempotency_key:
                        return existing
            known_ids = {existing.id for existing in existing_jobs}
            missing = [dependency for dependency in job.dependencies if dependency not in known_ids]
            if missing:
                raise DomainValidationError(f"unknown dependencies: {', '.join(missing)}")
            self._assert_acyclic(existing_jobs, job)
            return self._repo.create(job)

    @staticmethod
    def _assert_acyclic(existing: list[ScheduledJob], candidate: ScheduledJob) -> None:
        """Reject dependency cycles at submission rather than queueing forever."""
        graph = {item.id: list(item.dependencies) for item in existing}
        graph[candidate.id] = list(candidate.dependencies)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise DomainValidationError("dependency cycle detected")
            if node in visited or node not in graph:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node)

    def cancel(self, job_id: str) -> ScheduledJob:
        job = self._require(job_id)
        if job.status is not ScheduledStatus.QUEUED:
            raise DomainValidationError(f"job {job_id!r} is not queued")
        job.status = ScheduledStatus.CANCELLED
        job.touch()
        updated = self._repo.update(job)
        self._note(job, "scheduler.cancel", "CANCELLED")
        return updated

    def tick(self, now: Optional[datetime] = None) -> list[ScheduledJob]:
        moment = now or utcnow()
        return [self._run_one(job, moment) for job in self._ready(moment)]

    def resume_interrupted(self) -> list[ScheduledJob]:
        reset = []
        for job in self._repo.list():
            if job.status is ScheduledStatus.RUNNING:
                job.status = ScheduledStatus.QUEUED
                job.touch()
                self._repo.update(job)
                reset.append(job)
        return reset

    def pending(self) -> list[ScheduledJob]:
        return self._ready(utcnow(), apply_deadlines=False)

    def _ready(self, moment: datetime, apply_deadlines: bool = True) -> list[ScheduledJob]:
        by_id = {job.id: job for job in self._repo.list()}
        ready = []
        for job in by_id.values():
            if job.status is not ScheduledStatus.QUEUED:
                continue
            if job.deadline is not None and moment > job.deadline:
                if apply_deadlines:
                    job.status = ScheduledStatus.FAILED
                    job.result_summary = "deadline missed before execution"
                    self._repo.update(job)
                    self._note(job, "scheduler.deadline", "FAILED")
                continue
            deps = [by_id.get(dep) for dep in job.dependencies]
            if any(dep is None or dep.status is not ScheduledStatus.SUCCEEDED for dep in deps):
                continue
            if self._in_quiet_hours(moment):
                continue
            ready.append(job)
        ready.sort(key=lambda job: (-job.priority, job.created_at, job.id))
        return ready

    def _run_one(self, job: ScheduledJob, moment: datetime) -> ScheduledJob:
        job.status = ScheduledStatus.RUNNING
        job.attempts += 1
        job.touch()
        self._repo.update(job)
        if self._dispatch is None:
            return self._finish(job, ScheduledStatus.FAILED,
                                "scheduler has no dispatch executor configured",
                                "scheduler.dispatch")
        try:
            target = self._router.route(job.requirements)
        except RoutingError as exc:
            return self._finish(job, ScheduledStatus.FAILED, f"routing failed: {exc}", "scheduler.route")
        job.routed_provider = target.provider
        self._repo.update(job)
        try:
            summary = self._dispatch(job, target)
        except Exception as exc:  # noqa: BLE001
            if job.attempts < job.max_attempts:
                job.status = ScheduledStatus.QUEUED
                job.result_summary = f"attempt {job.attempts} failed: {exc}"
                self._repo.update(job)
                self._note(job, "scheduler.retry", "QUEUED")
                return job
            return self._finish(job, ScheduledStatus.FAILED,
                                f"attempts exhausted: {exc}", "scheduler.retry")
        return self._finish(job, ScheduledStatus.SUCCEEDED, summary, "scheduler.dispatch")

    def _finish(self, job: ScheduledJob, status: ScheduledStatus, summary: str, action: str) -> ScheduledJob:
        job.status = status
        job.result_summary = summary
        job.touch()
        updated = self._repo.update(job)
        self._note(job, action, status.value)
        return updated

    def _in_quiet_hours(self, moment: datetime) -> bool:
        if not self._quiet_hours:
            return False
        start, end = self._quiet_hours
        if start == end:
            return False
        hour = moment.hour
        return start <= hour < end if start < end else hour >= start or hour < end

    def _require(self, job_id: str) -> ScheduledJob:
        from ai_ecosystem.core.errors.exceptions import ResourceNotFoundError
        job = self._repo.get(job_id)
        if job is None:
            raise ResourceNotFoundError("ScheduledJob", job_id)
        return job

    def _note(self, job: ScheduledJob, action: str, outcome: str) -> None:
        if self._audit is None:
            return
        try:
            self._audit({"action": action, "task_id": job.id,
                         "resource": job.routed_provider or "scheduler",
                         "decision": outcome, "result": job.result_summary,
                         "agent_id": job.agent_id})
        except Exception:  # noqa: BLE001
            pass


class SqliteScheduledJobRepository:
    """Durable scheduler jobs in the existing database."""

    def __init__(self, db: Any) -> None:
        from ai_ecosystem.core.persistence.sqlite import _SnapshotTable
        self._t = _SnapshotTable(db, "scheduler_jobs", ScheduledJob)

    def create(self, item: ScheduledJob) -> ScheduledJob:
        return self._t.create(item)

    def get(self, item_id: str) -> Optional[ScheduledJob]:
        return self._t.get(item_id)

    def update(self, item: ScheduledJob) -> ScheduledJob:
        return self._t.update(item)

    def delete(self, item_id: str) -> bool:
        return self._t.delete(item_id)

    def list(self) -> list[ScheduledJob]:
        return self._t.list()
