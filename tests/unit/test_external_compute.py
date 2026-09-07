"""Gates 27-28: external compute (mocked) + cloud-safe offline agent."""

import pytest

from ai_ecosystem.agent.executor import OverallStatus, ParallelExecutor
from ai_ecosystem.cloud import (
    CloudAgent,
    CloudAuthError,
    CloudStatus,
    CloudUnavailableError,
    CloudUnsafeError,
    ComputeJob,
    DatasetPolicy,
    DatasetPolicyError,
    JobStatus,
    KaggleProvider,
    LightningProvider,
    MockNotebookTransport,
    MockSyncTransport,
    SqliteComputeJobRepository,
    SyncManager,
    SyncState,
    cloud_safe_plan,
    make_sync_object,
)
from ai_ecosystem.core.errors import DomainValidationError
from ai_ecosystem.core.models import Plan, PlanStep, Tool, ToolResult
from ai_ecosystem.core.models.enums import RiskLevel
from ai_ecosystem.core.persistence import Database
from ai_ecosystem.core.secrets import DictSecretsProvider
from ai_ecosystem.tools import GrantAllAuthorizer, ToolRegistry, ToolRunner


def _ok(args):
    return ToolResult(success=True, output="ok")


def _registry():
    registry = ToolRegistry()
    registry.register(Tool(name="compute", input_schema={"required": []},
                           risk_level=RiskLevel.LOW), _ok)
    return registry


def _plan(*tools):
    steps = [PlanStep(id=f"s{i}", description=t, dependencies=[],
                      tools=[t], verification="v", completion_criteria="c")
             for i, t in enumerate(tools)]
    return Plan(goal="g", steps=steps, final_verification="v")


def _provider(cls, **kwargs):
    secrets = {"KAGGLE_API_KEY": "k", "LIGHTNING_API_KEY": "l"}
    return cls(MockNotebookTransport(**kwargs), DictSecretsProvider(secrets))


def test_kaggle_authentication():
    provider = _provider(KaggleProvider)
    assert provider.connect().status is CloudStatus.CONNECTED
    assert provider.name == "kaggle"
    bad = KaggleProvider(MockNotebookTransport(),
                         DictSecretsProvider({}))
    with pytest.raises(CloudAuthError):
        bad.connect()


def test_lightning_authentication():
    provider = _provider(LightningProvider)
    assert provider.connect().status is CloudStatus.CONNECTED
    assert "lightning-mock" in provider.capabilities().available_models


@pytest.mark.parametrize("cls", [KaggleProvider, LightningProvider])
def test_submission_and_polling(cls, tmp_path):
    db = Database(str(tmp_path / "jobs.db"))
    db.migrate()
    try:
        provider = _provider(cls)
        provider.connect()
        repo = SqliteComputeJobRepository(db)
        job = repo.submit(ComputeJob(provider=cls.name, task_id="t1"))
        job = provider.submit_job(job)
        assert job.status is JobStatus.QUEUED
        for _ in range(5):
            job = provider.poll_job(job)
            repo._t.update(job)
        assert job.status is JobStatus.SUCCEEDED
        assert provider.job_result(job) == "mock://result/1"
        assert "mock" in provider.job_logs(job)
    finally:
        db.close()


@pytest.mark.parametrize("cls", [KaggleProvider, LightningProvider])
def test_failure(cls):
    provider = _provider(cls, outcome="FAILED")
    provider.connect()
    job = provider.submit_job(ComputeJob(provider=cls.name, task_id="t1"))
    for _ in range(5):
        job = provider.poll_job(job)
    assert job.status is JobStatus.FAILED


@pytest.mark.parametrize("cls", [KaggleProvider, LightningProvider])
def test_cancellation(cls):
    provider = _provider(cls)
    provider.connect()
    job = provider.submit_job(ComputeJob(provider=cls.name, task_id="t1"))
    cancelled = provider.cancel_job(job)
    assert cancelled.status is JobStatus.CANCELLED
    with pytest.raises(DomainValidationError):
        provider.cancel_job(cancelled)


@pytest.mark.parametrize("cls", [KaggleProvider, LightningProvider])
def test_timeout(cls):
    provider = _provider(cls, ticks_to_timeout=1, ticks_to_finish=99)
    provider.connect()
    job = provider.submit_job(ComputeJob(provider=cls.name, task_id="t1"))
    for _ in range(4):
        job = provider.poll_job(job)
    assert job.status is JobStatus.TIMED_OUT


def test_credentials_never_surface():
    provider = _provider(KaggleProvider)
    provider.connect()
    described = provider.status().model_dump_json()
    assert "KAGGLE_API_KEY" not in described
    assert provider.health()["reachable"] is True


def test_data_policy_rejection():
    policy = DatasetPolicy()
    policy.check("notes.csv", "dataset", 100, {"rows": 10})  # fine
    with pytest.raises(DatasetPolicyError, match="kind"):
        policy.check("app.exe", "binary", 100)
    with pytest.raises(DatasetPolicyError, match="exceeds"):
        policy.check("big.csv", "dataset", 10_000_001)
    with pytest.raises(DatasetPolicyError, match="credentials"):
        policy.check("leak.csv", "dataset", 100, {"api_key": "SECRET"})
    provider = _provider(KaggleProvider)
    provider.connect()
    job = ComputeJob(provider="kaggle", task_id="t1")
    with pytest.raises(DatasetPolicyError):
        provider.submit_job(job, datasets=[{"name": "leak", "kind": "dataset",
                                            "size_bytes": 10,
                                            "payload": {"password": "x"}}])


def test_provider_outage():
    provider = _provider(KaggleProvider, fail_submit=True)
    provider.connect()
    with pytest.raises(CloudUnavailableError):
        provider.submit_job(ComputeJob(provider="kaggle", task_id="t1"))


def test_restart_recovery(tmp_path):
    path = str(tmp_path / "jobs.db")
    first = Database(path)
    first.migrate()
    repo = SqliteComputeJobRepository(first)
    job = repo.submit(ComputeJob(provider="kaggle", task_id="t9"))
    repo.mark(job.id, JobStatus.RUNNING)
    first.close()
    second = Database(path)
    second.migrate()
    try:
        loaded = SqliteComputeJobRepository(second).require(job.id)
    finally:
        second.close()
    assert loaded.status is JobStatus.RUNNING
    # A fresh provider can keep polling the recovered job after
    # re-registering it remotely (the row survived; the session did not).
    provider = _provider(KaggleProvider)
    provider.connect()
    provider.submit_job(loaded)
    for _ in range(5):
        loaded = provider.poll_job(loaded)
    assert loaded.status is JobStatus.SUCCEEDED


def _cloud_agent(**kwargs):
    registry = _registry()
    runner = ToolRunner(registry, GrantAllAuthorizer())
    kwargs.setdefault("sync", SyncManager(transport=MockSyncTransport()))
    return CloudAgent(
        lambda: ParallelExecutor(runner, registry), **kwargs)


def test_pc_offline_cloud_continuation():
    agent = _cloud_agent(allowed_tools=["compute"])
    plan = _plan("compute")
    agent.accept("t1", plan, [make_sync_object("task", "t1", {"state": "x"})])
    result = agent.run_cloud_task("t1", plan)
    assert result.status is OverallStatus.COMPLETED


def test_local_only_task_rejection():
    agent = _cloud_agent(allowed_tools=["compute"])
    plan = _plan("compute")
    with pytest.raises(CloudUnsafeError, match="LOCAL_ONLY"):
        agent.accept("t1", plan, [make_sync_object("file", "/etc/passwd",
                                                   {"path": "/etc/passwd"})])


def test_cloud_safe_task_execution():
    agent = _cloud_agent(allowed_tools=["compute"])
    assert cloud_safe_plan(_plan("compute"), {"compute"})[0] is True
    assert cloud_safe_plan(_plan("terminal.execute"), {"compute"})[0] is False


def test_reconnect_and_conflict():
    agent = _cloud_agent()
    assert agent.resume_handoff("t", "v1", "v1") == "resume"
    assert agent.resume_handoff("t", "v1", "v2") == "conflict"


def test_task_resume_no_duplicates(tmp_path):
    transport = MockSyncTransport()
    sync = SyncManager(transport=transport)
    agent = _cloud_agent(sync=sync, allowed_tools=["compute"])
    plan = _plan("compute")
    objects = [make_sync_object("task", "t1", {"state": "done"})]
    agent.accept("t1", plan, objects)
    result = agent.run_cloud_task("t1", plan)
    assert result.status is OverallStatus.COMPLETED
    first = agent.publish_results("t1", result, objects)
    second = agent.publish_results("t1", result, objects)
    assert first.results[0].state is SyncState.UPLOADED
    assert second.results[0].state is SyncState.IN_SYNC
    assert transport.pushes == 1  # resume never duplicates


def test_crash_recovery(tmp_path):
    db = Database(str(tmp_path / "jobs.db"))
    db.migrate()
    try:
        repo = SqliteComputeJobRepository(db)
        job = repo.submit(ComputeJob(provider="lightning", task_id="t1"))
        # Crash after submit, before completion: job row survives.
    finally:
        db.close()
    reopened = Database(str(tmp_path / "jobs.db"))
    reopened.migrate()
    try:
        loaded = SqliteComputeJobRepository(reopened).require(job.id)
        assert loaded.status is JobStatus.QUEUED
    finally:
        reopened.close()
