"""Kaggle + Lightning AI providers behind CloudProvider (Gate 27).

Mock transports only: CI never touches real accounts. Dataset upload
is gated by DatasetPolicy (classification -> privacy -> permission):
unclassified, oversized, or secret-bearing payloads are rejected
before any submission, and credentials follow the same secrets rules
as OCI (never in logs, events, prompts, or messages).
"""

from __future__ import annotations

import time
from typing import Any

from ai_ecosystem.cloud.jobs import ComputeJob, JobStatus
from ai_ecosystem.cloud.providers import (
    CloudAuthError,
    CloudCapabilities,
    CloudError,
    CloudProvider,
    CloudStatus,
    CloudUnavailableError,
    ProviderInfo,
)
from ai_ecosystem.core.errors.exceptions import DomainValidationError
from ai_ecosystem.core.secrets import SecretsProvider, looks_secret


class DatasetPolicyError(CloudError):
    """A dataset/artifact was refused by data policy."""


class DatasetPolicy:
    """Upload gate: kind allow-list, size cap, secret scan."""

    def __init__(
        self,
        allowed_kinds: list[str] | None = None,
        max_bytes: int = 10_000_000,
    ) -> None:
        self.allowed_kinds = list(allowed_kinds or ["dataset", "notebook", "model"])
        self.max_bytes = max_bytes

    def check(self, name: str, kind: str, size_bytes: int, payload: Any = None) -> None:
        """Raise DatasetPolicyError unless the artifact may leave the PC."""
        if kind not in self.allowed_kinds:
            raise DatasetPolicyError(f"dataset kind {kind!r} is not uploadable")
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool):
            try:
                size_bytes = int(size_bytes)
            except (TypeError, ValueError):
                raise DatasetPolicyError(f"dataset {name!r} has an invalid size") from None
        if size_bytes < 0:
            raise DatasetPolicyError(f"dataset {name!r} has a negative size")
        if size_bytes > self.max_bytes:
            raise DatasetPolicyError(
                f"dataset {name!r} ({size_bytes} bytes) exceeds {self.max_bytes} limit"
            )
        if payload is not None and _contains_secrets(payload):
            raise DatasetPolicyError(f"dataset {name!r} appears to contain credentials")


def _contains_secrets(payload: Any) -> bool:
    from ai_ecosystem.core.secrets import looks_like_secret_value

    if isinstance(payload, dict):
        return any(
            looks_secret(str(key)) or _contains_secrets(value) for key, value in payload.items()
        )
    if isinstance(payload, list | tuple):
        return any(_contains_secrets(item) for item in payload)
    return looks_like_secret_value(payload)


class MockNotebookTransport:
    """Scripted notebook backend: jobs advance only when polled.

    Lifecycle: CREATED -> QUEUED -> RUNNING -> COMPLETED (or FAILED /
    CANCELLED / TIMED_OUT on script). Timeouts are virtual: each status
    poll counts as one tick against ``ticks_to_timeout``.
    """

    def __init__(
        self,
        fail_handshake: bool = False,
        fail_submit: bool = False,
        outcome: str = "COMPLETED",
        ticks_to_finish: int = 2,
        ticks_to_timeout: int = 1_000_000,
        logs_text: str = "mock logs",
        result_ref: str = "mock://result/1",
    ) -> None:
        self.fail_handshake = fail_handshake
        self.fail_submit = fail_submit
        self.outcome = outcome
        self.ticks_to_finish = ticks_to_finish
        self.ticks_to_timeout = ticks_to_timeout
        self.logs_text = logs_text
        self.result_ref = result_ref
        self.calls = {"handshake": 0, "submit": 0, "status": 0, "cancel": 0, "logs": 0, "result": 0}
        self._jobs: dict[str, dict] = {}

    def handshake(self, credential: str) -> dict[str, Any]:
        """Authenticate (credential acknowledged, never echoed)."""
        self.calls["handshake"] += 1
        if self.fail_handshake:
            raise CloudUnavailableError("mock backend unavailable")
        return {"authenticated": True}

    def submit(self, job_id: str, spec: dict) -> None:
        """Queue a job (starts CREATED, polled forward)."""
        self.calls["submit"] += 1
        if self.fail_submit:
            raise CloudUnavailableError("mock backend cannot accept jobs")
        self._jobs[job_id] = {"state": "CREATED", "ticks": 0, "spec": dict(spec)}

    def status(self, job_id: str) -> str:
        """Advance one tick and report the lifecycle state."""
        self.calls["status"] += 1
        job = self._jobs.get(job_id)
        if job is None:
            raise CloudError(f"unknown job {job_id!r}")
        flow = ["CREATED", "QUEUED", "RUNNING"]
        job["ticks"] += 1
        if job["ticks"] > self.ticks_to_timeout:
            job["state"] = "TIMED_OUT"
        elif job["state"] in flow:
            job["state"] = flow[min(flow.index(job["state"]) + 1, 2)]
            if job["state"] == "RUNNING" and job["ticks"] > self.ticks_to_finish:
                job["state"] = self.outcome
        return job["state"]

    def cancel(self, job_id: str) -> None:
        """Cancel unless already terminal."""
        self.calls["cancel"] += 1
        job = self._jobs.get(job_id)
        if job is None:
            raise CloudError(f"unknown job {job_id!r}")
        if job["state"] not in ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"):
            job["state"] = "CANCELLED"

    def logs(self, job_id: str) -> str:
        """Fetch logs (raises for unknown jobs)."""
        self.calls["logs"] += 1
        if job_id not in self._jobs:
            raise CloudError(f"unknown job {job_id!r}")
        return self.logs_text

    def result(self, job_id: str) -> str:
        """Fetch the result reference (completed jobs only)."""
        self.calls["result"] += 1
        job = self._jobs.get(job_id)
        if job is None:
            raise CloudError(f"unknown job {job_id!r}")
        if job["state"] != "COMPLETED":
            raise CloudError(f"job {job_id!r} has no result ({job['state']})")
        return self.result_ref

    def close(self) -> None:
        """Release (mock: nothing held)."""


class ExternalProvider(CloudProvider):
    """Shared notebook-provider behavior for Kaggle and Lightning."""

    credential_name = "API_KEY"
    display_models = ("external-mock",)

    def __init__(
        self,
        transport: MockNotebookTransport,
        secrets: SecretsProvider,
        dataset_policy: DatasetPolicy | None = None,
        region: str = "",
    ) -> None:
        self._transport = transport
        self._secrets = secrets
        self._datasets = dataset_policy or DatasetPolicy()
        self._region = region
        self._status = CloudStatus.DISCONNECTED
        self._latency_ms: float | None = None

    def connect(self) -> ProviderInfo:
        """Authenticate (credential values never surface)."""
        from ai_ecosystem.core.secrets import CredentialError

        self._status = CloudStatus.CONNECTING
        try:
            credential = self._secrets.require(self.credential_name)
            started = time.monotonic()
            self._transport.handshake(credential)
            self._latency_ms = round((time.monotonic() - started) * 1000.0, 2)
        except CloudUnavailableError:
            self._status = CloudStatus.ERROR
            raise
        except CredentialError as exc:
            self._status = CloudStatus.ERROR
            raise CloudAuthError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            self._status = CloudStatus.ERROR
            raise CloudAuthError(f"{self.name} authentication failed") from exc
        self._status = CloudStatus.CONNECTED
        return self.status()

    def disconnect(self) -> None:
        """Close quietly (safe to call twice)."""
        try:
            self._transport.close()
        finally:
            self._status = CloudStatus.DISCONNECTED

    def status(self) -> ProviderInfo:
        """Describe without secrets."""
        return ProviderInfo(
            provider=self.name,
            status=self._status,
            region=self._region,
            capabilities=self.capabilities()
            if self._status is CloudStatus.CONNECTED
            else CloudCapabilities(),
            latency_ms=self._latency_ms,
        )

    def capabilities(self) -> CloudCapabilities:
        """Declared endpoint facts (overridden per provider)."""
        return CloudCapabilities()

    def health(self) -> dict[str, Any]:
        """Bounded health determination."""
        try:
            self._transport.handshake("health-check")
            reachable = True
        except Exception:  # noqa: BLE001
            reachable = False
        available = reachable and self._status is CloudStatus.CONNECTED
        return {
            "provider": self.name,
            "reachable": reachable,
            "available": available,
            "status": self._status.value
            if available
            else (CloudStatus.DEGRADED.value if reachable else CloudStatus.ERROR.value),
        }

    def submit_job(self, job: ComputeJob, datasets: list[dict] | None = None) -> ComputeJob:
        """Policy-gate datasets, then submit (connected only)."""
        if self._status is not CloudStatus.CONNECTED:
            raise CloudUnavailableError(f"{self.name} is not connected")
        for dataset in datasets or []:
            self._datasets.check(
                str(dataset.get("name", "")),
                str(dataset.get("kind", "")),
                int(dataset.get("size_bytes", 0)),
                dataset.get("payload"),
            )
        job.provider = self.name
        self._transport.submit(job.id, {"task_id": job.task_id})
        job.status = JobStatus.QUEUED
        return job

    def poll_job(self, job: ComputeJob) -> ComputeJob:
        """Advance one tick and map the lifecycle onto the job."""
        mapping = {
            "CREATED": JobStatus.QUEUED,
            "QUEUED": JobStatus.QUEUED,
            "RUNNING": JobStatus.RUNNING,
            "COMPLETED": JobStatus.SUCCEEDED,
            "FAILED": JobStatus.FAILED,
            "CANCELLED": JobStatus.CANCELLED,
            "TIMED_OUT": JobStatus.TIMED_OUT,
        }
        try:
            state = self._transport.status(job.id)
        except CloudError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CloudUnavailableError(f"{self.name} poll failed") from exc
        job.status = mapping.get(state, JobStatus.FAILED)
        if job.status is JobStatus.RUNNING and job.started_at is None:
            from ai_ecosystem.core.models.base import utcnow

            job.started_at = utcnow()
        if job.status in (
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.TIMED_OUT,
        ):
            from ai_ecosystem.core.models.base import utcnow

            job.completed_at = utcnow()
            if job.status is JobStatus.SUCCEEDED:
                job.result_reference = self._transport.result(job.id)
            else:
                job.error = f"remote state {state}"
        job.touch()
        return job

    def cancel_job(self, job: ComputeJob) -> ComputeJob:
        """Cancel remotely and locally (terminal jobs refuse)."""
        if job.status in (
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.TIMED_OUT,
        ):
            raise DomainValidationError(f"job {job.id!r} already terminal")
        try:
            self._transport.cancel(job.id)
        except CloudError:
            raise
        except Exception as exc:  # noqa: BLE001 -- keep stores consistent
            raise CloudUnavailableError(f"{self.name} cancel failed: {exc}") from exc
        job.status = JobStatus.CANCELLED
        from ai_ecosystem.core.models.base import utcnow

        job.completed_at = utcnow()
        job.touch()
        return job

    def job_logs(self, job: ComputeJob) -> str:
        """Fetch remote logs."""
        return self._transport.logs(job.id)

    def job_result(self, job: ComputeJob) -> str:
        """Fetch the result reference (completed jobs only)."""
        return self._transport.result(job.id)


class KaggleProvider(ExternalProvider):
    """Kaggle notebooks behind the provider seam."""

    name = "kaggle"
    credential_name = "KAGGLE_API_KEY"

    def capabilities(self) -> CloudCapabilities:
        """Kaggle's free-tier shape (declared, mock-backed)."""
        return CloudCapabilities(
            cpu="4 CPU",
            memory_gb=16.0,
            gpu="Tesla P100",
            storage_gb=20.0,
            network=False,
            available_models=["kaggle-mock"],
            available_tools=["notebook.execute"],
        )


class LightningProvider(ExternalProvider):
    """Lightning AI studios behind the provider seam."""

    name = "lightning"
    credential_name = "LIGHTNING_API_KEY"

    def capabilities(self) -> CloudCapabilities:
        """Lightning studio shape (declared, mock-backed)."""
        return CloudCapabilities(
            cpu="8 CPU",
            memory_gb=32.0,
            gpu="A10G",
            storage_gb=50.0,
            network=True,
            available_models=["lightning-mock"],
            available_tools=["studio.execute"],
        )
