"""Gate 20: desktop boundary -- submission, status, cancel, events, safety."""

import json
from pathlib import Path

import pytest

from ai_ecosystem.core.events import InMemoryEventStore
from ai_ecosystem.core.models.enums import EventType
from ai_ecosystem.core.runtime import AgentRuntime
from ai_ecosystem.interface import (
    ApiClient,
    ApiError,
    ApiUnavailableError,
    LocalHttpServer,
    RuntimeAPI,
)

DESKTOP = Path(__file__).resolve().parents[2] / "desktop"


@pytest.fixture()
def api(tmp_path):
    runtime = AgentRuntime(str(tmp_path / "desk.db"))
    store = InMemoryEventStore()
    store.attach(runtime.bus)
    yield RuntimeAPI(runtime, event_store=store), runtime, store
    runtime.shutdown()


@pytest.fixture(scope="module")
def served():
    """One shared HTTP server for the module (avoids socket churn)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        runtime = AgentRuntime(str(Path(tmp) / "desk.db"))
        store = InMemoryEventStore()
        store.attach(runtime.bus)
        server = LocalHttpServer(RuntimeAPI(runtime, event_store=store)).start()
        try:
            yield ApiClient(server.url)
        finally:
            server.stop()
            runtime.shutdown()


def test_1_runtime_starts(api):
    _, runtime, _ = api
    assert runtime.manager is not None


def test_2_ui_connects_to_runtime(served):
    assert served.health() == {"status": "ok"}


def test_3_task_submission_returns_id(served):
    created = served.submit("Research entry points.")
    assert created["task_id"]
    assert created["state"] == "CREATED"
    assert created["completed"] is False


def test_4_task_status_retrieval(api):
    runtime_api, _, _ = api
    created = runtime_api.create_task("Do things.")
    status = runtime_api.get_task_status(created["task_id"])
    assert status["state"] == "CREATED"
    assert "next" in status


def test_5_task_cancellation(api):
    runtime_api, _, _ = api
    created = runtime_api.create_task("Never mind.")
    cancelled = runtime_api.cancel_task(created["task_id"])
    assert cancelled["state"] == "CANCELLED"
    assert cancelled["completed"] is True
    with pytest.raises(ApiError):
        runtime_api.cancel_task(created["task_id"])  # already terminal


def test_6_event_retrieval(api):
    runtime_api, runtime, store = api
    created = runtime_api.create_task("Watched task.")
    events = runtime_api.get_task_events(created["task_id"])
    assert len(events) == 1
    assert events[0]["type"] == EventType.TASK_CREATED.value
    assert runtime_api.get_task_events(created["task_id"], since=5) == []


def test_7_runtime_unavailable():
    with pytest.raises(ApiUnavailableError):
        ApiClient("http://127.0.0.1:1").health()


def test_8_malformed_request_rejection(api, served):
    runtime_api, _, _ = api
    for bad in ("", "   ", 123, None, "x" * 5000):
        with pytest.raises(ApiError):
            runtime_api.create_task(bad)
    with pytest.raises(ApiError):
        runtime_api.get_task("")
    with pytest.raises(ApiError):
        served.post("/tasks", {"goal": 123})
    with pytest.raises(ApiError):
        served.get("/nope")


def test_9_pause_unsupported_not_silent(api):
    runtime_api, _, _ = api
    created = runtime_api.create_task("Pausable?")
    with pytest.raises(ApiError, match="unsupported"):
        runtime_api.pause_task(created["task_id"])
    with pytest.raises(ApiError, match="unsupported"):
        runtime_api.resume_task(created["task_id"])


def _assert_http_error(callable_, attempts: int = 6) -> None:
    """Require ApiError, tolerating Windows loopback RST flakes.

    WinError 10053 intermittently aborts localhost connections under
    full-suite load. A transport abort is not the assertion under test
    (routing must 404 with ApiError), so abort retries the CALL with a
    short settle delay -- but the outcome is never weakened: success
    still fails the test, and exhausted retries fail loudly instead of
    passing silently.
    """
    import time as _time

    for _ in range(attempts):
        try:
            callable_()
        except ApiUnavailableError:
            _time.sleep(0.2)
            continue  # documented transport flake: retry the call
        except ApiError:
            return  # the required outcome
        raise AssertionError("expected ApiError, call unexpectedly succeeded")
    raise AssertionError(
        f"transport unstable after {attempts} attempts")


def test_10_frontend_cannot_directly_execute_tools(served):
    _assert_http_error(
        lambda: served.post("/tools/execute", {"tool": "terminal.execute"}))
    _assert_http_error(
        lambda: served.post("/policy/grant", {"tool": "terminal.execute"}))
    import ai_ecosystem.interface.server as module

    routes = open(module.__file__).read()
    assert "ToolRunner" not in routes
    assert "tool_handler" not in routes.lower()


def test_11_secrets_never_reach_frontend(api, tmp_path):
    from ai_ecosystem.intelligence import MockModelProvider, ModelCapabilities

    provider = MockModelProvider("secret-llm",
                                 ModelCapabilities(structured_output=True))
    runtime_api, _, _ = api
    runtime_api.create_task("Seed task.")
    wired = RuntimeAPI(runtime_api._runtime, models=[provider])
    assert wired.get_models() == [{"provider_id": "secret-llm",
                                   "capabilities": provider.capabilities.model_dump()}]
    assert wired.get_skills() == []
    assert wired.get_agent_status() == {"tasks": {"CREATED": 1}}


def test_12_awareness_unconfigured_is_explicit(api):
    runtime_api, _, _ = api
    with pytest.raises(ApiError, match="unavailable"):
        runtime_api.get_system_awareness()


def test_13_desktop_scaffold_integrity():
    package = json.loads((DESKTOP / "package.json").read_text())
    assert package["private"] is True
    assert "build" in package["scripts"] and "dev" in package["scripts"]
    tauri = json.loads((DESKTOP / "src-tauri" / "tauri.conf.json").read_text())
    assert tauri["app"]["withGlobalTauri"] is True
    for required in ("src/App.tsx", "src/api.ts", "src/main.tsx",
                     "src-tauri/Cargo.toml", "src-tauri/src/main.rs",
                     "tsconfig.json", "vite.config.ts", "index.html"):
        assert (DESKTOP / required).is_file(), required
    # The shell must supervise the backend: reuse a healthy one, else
    # spawn the console script, wait for the port, kill on exit, and
    # expose the outcome to the UI via a command.
    shell = (DESKTOP / "src-tauri" / "src" / "main.rs").read_text()
    for marker in ("ai-ecosystem-serve", "ai_ecosystem.interface.serve",
                   "backend_status", "generate_handler", "CloseRequested"):
        assert marker in shell, marker


def test_14_scaffold_contains_no_secrets_or_privileges():
    forbidden = ("api_key", "apikey", "secret", "token", "password",
                 "dangerouslySetInnerHTML", "__TAURI_INVOKE__(\"exec\"")
    # Vendored / generated trees are not the scaffold: dependency sources,
    # lockfiles, build output, and compiled artifacts cannot carry OUR
    # secrets (and their .d.ts files legitimately name token/password
    # props on other libraries' types).
    skipped = {"node_modules", "dist", "target", ".git"}
    hits = []
    for path in sorted(DESKTOP.rglob("*")):
        if any(part in skipped or part == "package-lock.json"
               for part in path.parts):
            continue
        if path.is_file() and path.suffix in {".ts", ".tsx", ".json", ".rs", ".html"}:
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            hits.extend(f"{path.name}:{word}" for word in forbidden if word in text)
    # 'token' legitimately appears in "CSRF token: none" docs; allow listed notes.
    # type="password" is the masked credential input in Settings: correct
    # practice (the value is never in source), so it is allow-listed here.
    hits = [h for h in hits if "csrf" not in h.lower()
            and h != "App.tsx:password"]
    assert hits == [], hits


def test_15_api_perf_smoke(served):
    import time

    started = time.monotonic()
    created = served.submit("Perf probe.")
    submit_s = time.monotonic() - started
    started = time.monotonic()
    events = served.get(f"/tasks/{created['task_id']}/events")
    event_s = time.monotonic() - started
    started = time.monotonic()
    served.get("/tasks")
    list_s = time.monotonic() - started
    print(f"\napi smoke: submit={submit_s:.3f}s events={event_s:.3f}s "
          f"list={list_s:.3f}s")
    assert events and submit_s < 5 and event_s < 5 and list_s < 5
