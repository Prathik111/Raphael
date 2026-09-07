"""Typed compute jobs over the existing database (Gate 23).

Gate 3's raw SqliteJobStore keeps storing untyped envelopes; typed
ComputeJobs live here in their own table. No duplication: raw envelopes
stay raw, typed jobs stay typed, and both share the same Database.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import Field

from ai_ecosystem.core.errors.exceptions import (
    DomainValidationError,
    ResourceNotFoundError,
)
from ai_ecosystem.core.models.base import Entity, utcnow


class JobStatus(str, Enum):
    """Lifecycle of one compute job."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class ComputeJob(Entity):
    """A unit of remote work with references, not raw payloads."""

    provider: str = ""
    task_id: str = ""
    requested_capabilities: list[str] = Field(default_factory=list)
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime = Field(default_factory=utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result_reference: str = ""
    error: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


_ALLOWED_MOVES: dict[JobStatus, frozenset[JobStatus]] = {
    # QUEUED->FAILED covers pre-run refusal (validation, auth, policy).
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.FAILED,
                                 JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED,
                                  JobStatus.CANCELLED, JobStatus.TIMED_OUT}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
    JobStatus.TIMED_OUT: frozenset(),
}


class SqliteComputeJobRepository:
    """Durable compute jobs (same database, own table)."""

    def __init__(self, db: Any) -> None:
        from ai_ecosystem.core.persistence.sqlite import _SnapshotTable

        self._t = _SnapshotTable(db, "compute_jobs", ComputeJob)

    def submit(self, job: ComputeJob) -> ComputeJob:
        """Queue a job (QUEUED only)."""
        if job.status is not JobStatus.QUEUED:
            raise DomainValidationError("only QUEUED jobs can be submitted")
        return self._t.create(job)

    def get(self, job_id: str) -> Optional[ComputeJob]:
        """Fetch by id (None when unknown)."""
        return self._t.get(job_id)

    def require(self, job_id: str) -> ComputeJob:
        """Fetch or raise (operators should fail loudly)."""
        job = self._t.get(job_id)
        if job is None:
            raise ResourceNotFoundError("ComputeJob", job_id)
        return job

    def mark(self, job_id: str, status: JobStatus, error: str = "",
             result_reference: str = "") -> ComputeJob:
        """Advance lifecycle with timestamps (strict transition map)."""
        job = self.require(job_id)
        terminal = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED,
                    JobStatus.TIMED_OUT}
        if status not in _ALLOWED_MOVES.get(job.status, frozenset()):
            raise DomainValidationError(
                f"illegal job transition: {job.status.value} -> {status.value}")
        job.status = status
        job.error = error
        if result_reference:
            job.result_reference = result_reference
        now = utcnow()
        if status is JobStatus.RUNNING and job.started_at is None:
            job.started_at = now
        if status in terminal:
            job.completed_at = now
        job.touch()
        return self._t.update(job)

    def cancel(self, job_id: str) -> ComputeJob:
        """Cancel a non-terminal job."""
        job = self.require(job_id)
        if job.status in (JobStatus.SUCCEEDED, JobStatus.FAILED,
                          JobStatus.CANCELLED, JobStatus.TIMED_OUT):
            raise DomainValidationError(f"job {job_id!r} already terminal")
        return self.mark(job_id, JobStatus.CANCELLED)

    def list(self, task_id: str = "") -> list[ComputeJob]:
        """Jobs, optionally for one task, oldest first."""
        jobs = self._t.list()
        if task_id:
            jobs = [job for job in jobs if job.task_id == task_id]
        return sorted(jobs, key=lambda job: job.created_at)
