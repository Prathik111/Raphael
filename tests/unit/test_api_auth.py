"""API authentication + CORS allowlist (review fix 01)."""

import urllib.request

import pytest

from ai_ecosystem.core.config import AppConfig
from ai_ecosystem.interface import ApiClient, LocalHttpServer, RuntimeAPI
from ai_ecosystem.core.events import InMemoryEventStore
from ai_ecosystem.core.runtime import AgentRuntime
from ai_ecosystem.interface.serve import build_stack, load_or_create_token


@pytest.fixture(autouse=True)
def _no_ambient_model(monkeypatch):
    """Tests must never reach a real model from ambient environment."""
    for var in ("AI_ECO_MODEL_ENDPOINT", "AI_ECO_MODEL_API_KEY",
                "AI_ECO_MODEL_NAME"):
        monkeypatch.delenv(var, raising=False)


def _stack(tmp_path, token="test-token-123"):
    config = AppConfig(db_path=str(tmp_path / "auth.db"), api_port=0)
    stack = build_stack(config, workspace=str(tmp_path / "ws"),
                        auth_token=token)
    return stack


def test_missing_token_rejected(tmp_path):
    stack = _stack(tmp_path)
    server = stack.server.start()
    try:
        with pytest.raises(Exception):
            ApiClient(server.url).health()
    finally:
        server.stop()
        stack.runtime.shutdown()


def test_wrong_token_rejected(tmp_path):
    stack = _stack(tmp_path)
    server = stack.server.start()
    try:
        with pytest.raises(Exception):
            ApiClient(server.url, auth_token="wrong").health()
    finally:
        server.stop()
        stack.runtime.shutdown()


def test_correct_token_accepted(tmp_path):
    stack = _stack(tmp_path)
    server = stack.server.start()
    try:
        client = ApiClient(server.url, auth_token="test-token-123")
        assert client.health() == {"status": "ok"}
        created = client.submit("authed task")
        assert created["task_id"]
    finally:
        server.stop()
        stack.runtime.shutdown()


def test_preflight_needs_no_token_but_grants_listed_origins(tmp_path):
    stack = _stack(tmp_path)
    server = stack.server.start()
    try:
        base = server.url

        def options(origin):
            req = urllib.request.Request(base + "/tasks", method="OPTIONS",
                                         headers={"Origin": origin})
            with urllib.request.urlopen(req) as response:
                return response.status, dict(response.headers)

        status, headers = options("http://tauri.localhost")
        assert status == 204
        assert headers.get("Access-Control-Allow-Origin") == \
            "http://tauri.localhost"
        _, evil = options("https://evil.test")
        assert "Access-Control-Allow-Origin" not in evil
    finally:
        server.stop()
        stack.runtime.shutdown()


def test_cors_grant_requires_allowlisted_origin(tmp_path):
    stack = _stack(tmp_path)
    server = stack.server.start()
    try:
        base = server.url
        req = urllib.request.Request(
            base + "/health",
            headers={"Origin": "https://evil.test",
                     "Authorization": "Bearer test-token-123"})
        with urllib.request.urlopen(req) as response:
            assert "Access-Control-Allow-Origin" not in dict(response.headers)
        good = urllib.request.Request(
            base + "/health",
            headers={"Origin": "http://tauri.localhost",
                     "Authorization": "Bearer test-token-123"})
        with urllib.request.urlopen(good) as response:
            assert dict(response.headers).get(
                "Access-Control-Allow-Origin") == "http://tauri.localhost"
    finally:
        server.stop()
        stack.runtime.shutdown()


def test_token_file_created_and_reused(tmp_path):
    path = str(tmp_path / "api_token")
    first = load_or_create_token(path)
    assert len(first) >= 32
    assert load_or_create_token(path) == first
    assert open(path).read() == first


def test_no_auth_flag_is_open_by_default(tmp_path):
    """build_stack without a token stays open (tests keep working)."""
    config = AppConfig(db_path=str(tmp_path / "open.db"), api_port=0)
    stack = build_stack(config, workspace=str(tmp_path / "ws"))
    server = stack.server.start()
    try:
        assert ApiClient(server.url).health() == {"status": "ok"}
    finally:
        server.stop()
        stack.runtime.shutdown()
