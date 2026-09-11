"""Backend launcher: stack composition, dispatch, CORS (all local)."""

import time

import pytest

from ai_ecosystem.core.config import AppConfig
from ai_ecosystem.interface.serve import build_stack


@pytest.fixture(autouse=True)
def _no_ambient_model(monkeypatch):
    """Tests must never reach a real model from ambient environment."""
    for var in ("AI_ECO_MODEL_ENDPOINT", "AI_ECO_MODEL_API_KEY", "AI_ECO_MODEL_NAME"):
        monkeypatch.delenv(var, raising=False)


def test_stack_serves_on_configured_loopback(tmp_path):
    config = AppConfig(db_path=str(tmp_path / "serve.db"), api_port=0)
    stack = build_stack(config, workspace=str(tmp_path / "ws"))
    server = stack.server.start()
    try:
        from ai_ecosystem.interface import ApiClient

        client = ApiClient(server.url)
        assert client.health() == {"status": "ok"}
        import urllib.request

        req = urllib.request.Request(
            server.url + "/health", headers={"Origin": "http://tauri.localhost"}
        )
        with urllib.request.urlopen(req) as response:
            assert (
                dict(response.headers).get("Access-Control-Allow-Origin")
                == "http://tauri.localhost"
            )
    finally:
        server.stop()
        stack.runtime.shutdown()


def _headers(base_url, path):
    import urllib.request

    with urllib.request.urlopen(base_url + path) as response:
        return {k.lower(): v for k, v in response.headers.items()}


def test_unconfigured_model_fails_tasks_loudly(tmp_path):
    config = AppConfig(db_path=str(tmp_path / "serve.db"), api_port=0)
    stack = build_stack(config, workspace=str(tmp_path / "ws"))
    try:
        assert stack.model_configured is False
        assert stack.agent._registry.get("agent.respond") is not None
        created = stack.api.create_task("do things")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = stack.api.get_task(created["task_id"])["state"]
            if state == "FAILED":
                break
            time.sleep(0.05)
        assert state == "FAILED"
        events = stack.api.get_task_events(created["task_id"])
        kinds = [e["type"] for e in events]
        assert "TaskCreated" in kinds and "TaskFailed" in kinds
        assert "AI_ECO_MODEL_API_KEY" in str(events[-1]["payload"])
    finally:
        stack.runtime.shutdown()


def test_task_status_route(tmp_path):
    config = AppConfig(db_path=str(tmp_path / "serve.db"), api_port=0)
    stack = build_stack(config, workspace=str(tmp_path / "ws"))
    server = stack.server.start()
    try:
        from ai_ecosystem.interface import ApiClient

        client = ApiClient(server.url)
        created = client.submit("status probe")
        status = client.get(f"/tasks/{created['task_id']}/status")
        assert status["task_id"] == created["task_id"]
        assert "next" in status
    finally:
        server.stop()
        stack.runtime.shutdown()


def test_models_endpoint_empty_without_provider(tmp_path):
    config = AppConfig(db_path=str(tmp_path / "serve.db"), api_port=0)
    stack = build_stack(config, workspace=str(tmp_path / "ws"))
    try:
        assert stack.api.get_models() == []
    finally:
        stack.runtime.shutdown()


def test_result_endpoint_serves_persisted_summary(tmp_path):
    config = AppConfig(db_path=str(tmp_path / "serve.db"), api_port=0)
    stack = build_stack(config, workspace=str(tmp_path / "ws"))
    try:
        created = stack.api.create_task("do things")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if stack.api.get_task(created["task_id"])["state"] == "FAILED":
                break
            time.sleep(0.05)
        result = stack.api.get_task_result(created["task_id"])
        assert result["task_id"] == created["task_id"]
        assert result["status"] == "FAILED"
        assert "AI_ECO_MODEL_API_KEY" in result["error"]
        assert result["reply"] == ""
    finally:
        stack.runtime.shutdown()


def test_result_unavailable_while_running(tmp_path):
    from ai_ecosystem.interface import ApiError

    config = AppConfig(db_path=str(tmp_path / "serve.db"), api_port=0)
    stack = build_stack(config, workspace=str(tmp_path / "ws"))
    try:
        created = stack.api.create_task("slow job")
        # Immediately: dispatch may not have finished; either a result
        # exists or the API says unavailable -- never a crash.
        try:
            result = stack.api.get_task_result(created["task_id"])
            assert result["task_id"] == created["task_id"]
        except ApiError as exc:
            assert exc.code == "unavailable"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = stack.api.get_task(created["task_id"])["state"]
            if state in ("COMPLETED", "FAILED", "CANCELLED"):
                break
            time.sleep(0.05)
    finally:
        stack.runtime.shutdown()


def test_cancel_stops_dispatch_and_marks_task(tmp_path, monkeypatch):
    import ai_ecosystem.interface.serve as serve_module
    from ai_ecosystem.core.errors import ModelUnavailableError
    from ai_ecosystem.intelligence import MockModelProvider

    def slow_provider(_request):
        time.sleep(5)
        raise ModelUnavailableError("slow model down")

    monkeypatch.setattr(
        serve_module.HttpChatModelProvider,
        "from_secrets",
        classmethod(
            lambda cls, secrets, **kw: MockModelProvider(
                "slow-mock", handler=slow_provider
            )
        ),
    )
    config = AppConfig(db_path=str(tmp_path / "serve.db"), api_port=0)
    stack = build_stack(config, workspace=str(tmp_path / "ws"))
    assert stack.model_configured is True
    server = stack.server.start()
    try:
        from ai_ecosystem.interface import ApiClient

        client = ApiClient(server.url)
        created = client.submit("cancel me")
        time.sleep(0.5)  # worker is blocked inside the model call
        cancelled = client.post(f"/tasks/{created['task_id']}/cancel")
        assert cancelled["state"] == "CANCELLED"
        assert cancelled["completed"] is True
        # Worker settles without resurrecting the task.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = client.get(f"/tasks/{created['task_id']}")["state"]
            assert state == "CANCELLED", f"task left CANCELLED: {state}"
            try:
                result = client.get(f"/tasks/{created['task_id']}/result")
            except Exception:
                time.sleep(0.2)  # worker still inside the slow model call
                continue
            assert "cancel" in result["error"].lower()
            return
        raise AssertionError("no cancelled result persisted")
    finally:
        server.stop()
        stack.runtime.shutdown()


def test_queue_overflow_rejected_with_429(tmp_path, monkeypatch):
    import ai_ecosystem.interface.serve as serve_module
    from ai_ecosystem.core.errors import ModelUnavailableError
    from ai_ecosystem.intelligence import MockModelProvider
    from ai_ecosystem.interface import ApiClient, ApiError

    def slow_provider(_request):
        time.sleep(10)
        raise ModelUnavailableError("slow model down")

    monkeypatch.setattr(
        serve_module.HttpChatModelProvider,
        "from_secrets",
        classmethod(
            lambda cls, secrets, **kw: MockModelProvider(
                "slow-mock", handler=slow_provider
            )
        ),
    )
    config = AppConfig(
        db_path=str(tmp_path / "queue.db"),
        api_port=0,
        max_workers=1,
        max_queued_tasks=1,
    )
    stack = build_stack(config, workspace=str(tmp_path / "ws"))
    server = stack.server.start()
    try:
        client = ApiClient(server.url)
        client.submit("job one")  # running
        client.submit("job two")  # queued
        with pytest.raises(ApiError) as excinfo:
            client.submit("job three")  # overflow
        assert excinfo.value.code == "queue_full"
    finally:
        server.stop()
        stack.runtime.shutdown()


def test_restart_settles_stranded_tasks(tmp_path):
    """Tasks left non-terminal by a dead process fail honestly on boot."""
    from ai_ecosystem.core.runtime import AgentRuntime

    db_path = str(tmp_path / "restart.db")
    first = AgentRuntime(db_path)
    try:
        stranded, _ = first.manager.create_task("half done", "half done")
        done, _ = first.manager.create_task("finished", "finished")
        from ai_ecosystem.core.models.enums import TaskState

        first.manager.transition(done.id, TaskState.CANCELLED)
    finally:
        first.shutdown()
    config = AppConfig(db_path=db_path, api_port=0)
    stack = build_stack(config, workspace=str(tmp_path / "ws"))
    try:
        from ai_ecosystem.core.models.enums import TaskState

        assert stack.api.get_task(stranded.id)["state"] == "FAILED"
        assert stack.api.get_task(done.id)["state"] == "CANCELLED"
        failures = [
            e
            for e in stack.api.get_task_events(stranded.id)
            if e["type"] == "TaskFailed"
        ]
        assert failures and "interrupted" in str(failures[-1]["payload"])
    finally:
        stack.runtime.shutdown()
