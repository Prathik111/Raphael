"""Gate 23: cloud abstraction, OCI mock, jobs, router, credential discipline."""

import logging
import time

import pytest

from ai_ecosystem.cloud import (
    CloudStatus,
    CloudUnavailableError,
    ComputeJob,
    JobStatus,
    MockOCITransport,
    OCIModelProvider,
    OCIProvider,
    SqliteComputeJobRepository,
)
from ai_ecosystem.core.errors import DomainValidationError, ModelUnavailableError
from ai_ecosystem.core.persistence import Database
from ai_ecosystem.core.secrets import DictSecretsProvider, redact
from ai_ecosystem.intelligence import (
    MockModelProvider,
    ModelCapabilities,
    ModelRequest,
    ModelResponse,
    ModelRouter,
    ProviderProfile,
    RoutingRequirements,
)


def _oci(failures=0, secrets=None, **kwargs):
    return OCIProvider(
        MockOCITransport(failures=failures, **kwargs),
        DictSecretsProvider(secrets or {"OCI_TENANCY": "ocid1.tenancy.mock"}),
    )


def test_1_provider_registration():
    provider = _oci()
    assert provider.name == "oci"
    assert provider.status().status is CloudStatus.DISCONNECTED


def test_2_connection():
    provider = _oci()
    info = provider.connect()
    assert info.status is CloudStatus.CONNECTED
    assert info.region == "mock-region"
    provider.disconnect()
    assert provider.status().status is CloudStatus.DISCONNECTED
    provider.disconnect()  # idempotent


def test_3_disconnection():
    provider = _oci()
    provider.connect()
    provider.disconnect()
    with pytest.raises(CloudUnavailableError):
        provider.complete_remote("m", "hi")


def test_4_health():
    provider = _oci()
    healthy = provider.health()
    assert healthy["reachable"] is True
    assert healthy["available"] is False  # not connected: degraded, honest
    provider.connect()
    assert provider.health()["available"] is True


def test_5_capabilities():
    provider = _oci()
    caps = provider.capabilities()
    assert "oci-mock" in caps.available_models


def test_6_authentication_failure():
    provider = OCIProvider(MockOCITransport(), DictSecretsProvider({}))
    with pytest.raises(Exception, match="not configured"):
        provider.connect()
    assert provider.status().status is CloudStatus.ERROR


def test_7_job_submission_and_8_status(tmp_path):
    db = Database(str(tmp_path / "cloud.db"))
    db.migrate()
    try:
        repo = SqliteComputeJobRepository(db)
        job = repo.submit(ComputeJob(provider="oci", task_id="t1"))
        assert job.status is JobStatus.QUEUED
        assert repo.get(job.id).task_id == "t1"
    finally:
        db.close()


def test_9_job_completion(tmp_path):
    db = Database(str(tmp_path / "cloud.db"))
    db.migrate()
    try:
        repo = SqliteComputeJobRepository(db)
        job = repo.submit(ComputeJob(provider="oci", task_id="t1"))
        repo.mark(job.id, JobStatus.RUNNING)
        done = repo.mark(job.id, JobStatus.SUCCEEDED, result_reference="oci://r/1")
        assert done.status is JobStatus.SUCCEEDED
        assert done.result_reference == "oci://r/1"
        assert done.started_at is not None and done.completed_at is not None
    finally:
        db.close()


def test_10_job_failure(tmp_path):
    db = Database(str(tmp_path / "cloud.db"))
    db.migrate()
    try:
        repo = SqliteComputeJobRepository(db)
        job = repo.submit(ComputeJob(provider="oci", task_id="t1"))
        failed = repo.mark(job.id, JobStatus.FAILED, error="out of capacity")
        assert failed.error == "out of capacity"
        with pytest.raises(DomainValidationError):
            repo.mark(job.id, JobStatus.RUNNING)  # terminal: no resurrection
    finally:
        db.close()


def test_11_timeout():
    provider = _oci(failures=99)
    provider._status = CloudStatus.CONNECTED
    started = time.monotonic()
    with pytest.raises(CloudUnavailableError):
        provider.complete_remote("m", "hi")
    assert time.monotonic() - started < 5  # fails fast, no hanging


def test_12_cancellation(tmp_path):
    db = Database(str(tmp_path / "cloud.db"))
    db.migrate()
    try:
        repo = SqliteComputeJobRepository(db)
        job = repo.submit(ComputeJob(provider="oci", task_id="t1"))
        cancelled = repo.cancel(job.id)
        assert cancelled.status is JobStatus.CANCELLED
        with pytest.raises(DomainValidationError):
            repo.cancel(job.id)
    finally:
        db.close()


def test_13_provider_fallback():
    router = ModelRouter()
    oci_models = OCIModelProvider(
        "oci-llm",
        _oci(failures=99),
        "oci-mock",
        capabilities=ModelCapabilities(structured_output=True, context_length=128000),
    )
    # Connect the mock so routing sees it; calls still fail -> fallback.
    from ai_ecosystem.cloud.providers import CloudStatus as CS

    oci_models._oci._status = CS.CONNECTED
    router.register(oci_models, ProviderProfile(provider_id="oci-llm", cost_per_1k=0.0))
    router.register(
        MockModelProvider("local", handler=lambda req: ModelResponse(text="local")),
        ProviderProfile(provider_id="local", local=True, cost_per_1k=1.0),
    )
    response = router.complete(RoutingRequirements(), ModelRequest(prompt="hi"))
    assert response.text == "local"  # OCI failed over deterministically


def test_14_credential_redaction(caplog):
    secrets = {
        "OCI_TENANCY": "ocid1.tenancy.SUPERSECRET",
        "OCI_API_KEY": "key-SUPERSECRET",
    }
    provider = _oci(secrets=secrets)
    described = provider.describe_redacted()
    assert "SUPERSECRET" not in str(described)
    assert redact({"api_key": "abc", "region": "here"}) == {
        "api_key": "***",
        "region": "here",
    }
    with caplog.at_level(logging.INFO):
        logging.getLogger("test").info("status: %s", described)
    assert "SUPERSECRET" not in caplog.text


def test_15_cloud_cannot_bypass_local_policy():
    import ai_ecosystem.cloud.providers as module

    imports = [
        line.strip()
        for line in open(module.__file__).read().splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert not any("ToolRunner" in line for line in imports), imports
    assert not any("AuthorizationManager" in line for line in imports), imports
    assert not any("PermissionEngine" in line for line in imports), imports
    provider = _oci()
    assert not hasattr(provider, "run_tool")
    assert not hasattr(provider, "authorize")


def test_16_runtime_survives_cloud_outage():
    provider = _oci(failures=99)
    with pytest.raises(CloudUnavailableError):
        provider.connect()
    # Local router path is untouched by the outage.
    router = ModelRouter()
    router.register(
        MockModelProvider("local", handler=lambda req: ModelResponse(text="fine")),
        ProviderProfile(provider_id="local", local=True),
    )
    assert (
        router.complete(RoutingRequirements(), ModelRequest(prompt="hi")).text == "fine"
    )


def test_17_persistence_and_18_restart(tmp_path):
    path = str(tmp_path / "cloud.db")
    first = Database(path)
    first.migrate()
    repo = SqliteComputeJobRepository(first)
    job = repo.submit(ComputeJob(provider="oci", task_id="t9"))
    repo.mark(job.id, JobStatus.RUNNING)
    first.close()
    second = Database(path)
    second.migrate()
    try:
        loaded = SqliteComputeJobRepository(second).require(job.id)
    finally:
        second.close()
    assert loaded.status is JobStatus.RUNNING
    assert loaded.task_id == "t9"


def test_oci_model_provider_planner_unchanged():
    from ai_ecosystem.agent.planner.backend import ModelReasoningBackend

    provider = _oci()
    provider.connect()
    oci_llm = OCIModelProvider("oci-llm", provider, "oci-mock")
    backend = ModelReasoningBackend(oci_llm)
    assert backend is not None  # planner accepts it like any provider
    with pytest.raises(ModelUnavailableError):
        oci_broken = OCIModelProvider("x", _oci(failures=99), "oci-mock")
        oci_broken._oci._status = CloudStatus.CONNECTED
        oci_broken.complete(ModelRequest(prompt="hi"))


def test_cloud_health_perf_smoke():
    provider = _oci()
    started = time.monotonic()
    for _ in range(50):
        provider.health()
    elapsed = time.monotonic() - started
    print(f"\ncloud smoke: 50 health checks in {elapsed:.2f}s")
    assert elapsed < 5
