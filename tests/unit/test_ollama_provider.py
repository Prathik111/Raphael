from __future__ import annotations

import json

import pytest

from ai_ecosystem.core.errors.exceptions import ModelUnavailableError
from ai_ecosystem.intelligence.models.ollama import (
    DEFAULT_ENDPOINT,
    OllamaModelProvider,
    OllamaSettings,
    list_ollama_models,
    normalize_ollama_endpoint,
)
from ai_ecosystem.intelligence.models.providers import ModelRequest, ModelToolDefinition


def test_ollama_endpoint_accepts_loopback_and_strips_compatibility_path() -> None:
    assert normalize_ollama_endpoint(DEFAULT_ENDPOINT) == DEFAULT_ENDPOINT
    assert normalize_ollama_endpoint("http://127.0.0.1:11434/v1/") == DEFAULT_ENDPOINT


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.com:11434",
        "http://192.168.1.10:11434",
        "http://169.254.169.254",
        "http://8.8.8.8:11434",
        "file:///etc/passwd",
        "http://127.0.0.1:11434/api/chat",
        "http://user:password@127.0.0.1:11434",
    ],
)
def test_ollama_endpoint_rejects_nonlocal_or_nonbase_urls(endpoint: str) -> None:
    with pytest.raises(ValueError):
        normalize_ollama_endpoint(endpoint)


def test_ollama_settings_validate_generation_ranges() -> None:
    settings = OllamaSettings(model="qwen3.5:4b", top_k=64, num_ctx=16384)
    assert settings.provider == "ollama"
    assert settings.top_k == 64
    assert settings.num_ctx == 16384
    with pytest.raises(ValueError):
        OllamaSettings(temperature=2.1)
    with pytest.raises(ValueError):
        OllamaSettings(num_ctx=128)


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.payload if size < 0 else self.payload[:size]


def test_list_ollama_models_reads_installed_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ai_ecosystem.intelligence.models.ollama._open_local",
        lambda *_args, **_kwargs: _Response(
            {"models": [{"name": "z-model:latest"}, {"name": "a-model:7b"}]}
        ),
    )
    assert list_ollama_models() == ["a-model:7b", "z-model:latest"]


def test_list_ollama_models_reports_unreachable_server(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr("ai_ecosystem.intelligence.models.ollama._open_local", fail)
    with pytest.raises(ModelUnavailableError, match="Ollama is unreachable"):
        list_ollama_models()


def test_complete_sends_ollama_generation_settings_and_parses_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: object, timeout: float) -> _Response:
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response(
            {
                "model": "test-model",
                "message": {
                    "role": "assistant",
                    "content": "Working.",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "read_file",
                                "arguments": {"path": "notes.txt"},
                            }
                        }
                    ],
                },
                "prompt_eval_count": 12,
                "eval_count": 4,
                "done_reason": "stop",
            }
        )

    monkeypatch.setattr("ai_ecosystem.intelligence.models.ollama._open_local", fake_urlopen)
    provider = OllamaModelProvider(
        OllamaSettings(
            model="test-model",
            temperature=0.3,
            top_p=0.8,
            top_k=23,
            repeat_penalty=1.2,
            num_ctx=16384,
            num_predict=777,
        )
    )
    response = provider.complete(
        ModelRequest(
            system="You are a helpful agent.",
            prompt="Read the notes.",
            tools=[
                ModelToolDefinition(
                    name="read_file",
                    description="Read a workspace file",
                    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
                )
            ],
        )
    )

    payload = captured["payload"]
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert payload["model"] == "test-model"
    assert payload["options"] == {
        "temperature": 0.3,
        "top_p": 0.8,
        "top_k": 23,
        "repeat_penalty": 1.2,
        "num_ctx": 16384,
        "num_predict": 777,
    }
    assert payload["messages"][0] == {"role": "system", "content": "You are a helpful agent."}
    assert response.text == "Working."
    assert response.model == "test-model"
    assert response.input_tokens == 12
    assert response.output_tokens == 4
    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].arguments == {"path": "notes.txt"}


def test_local_transport_disables_proxies_and_redirects(monkeypatch):
    import urllib.error
    import urllib.request

    from ai_ecosystem.intelligence.models.ollama import _NoRedirect, _open_local

    captured = []

    class Opener:
        def open(self, request, timeout):
            return "opened"

    def build(*handlers):
        captured.extend(handlers)
        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", build)
    request = urllib.request.Request(DEFAULT_ENDPOINT + "/api/tags")
    assert _open_local(request, 2) == "opened"
    assert captured[0].proxies == {}
    assert isinstance(captured[1], _NoRedirect)
    with pytest.raises(urllib.error.HTTPError):
        captured[1].redirect_request(request, None, 302, "Found", {}, "https://example.com")


def test_settings_routes_require_auth_and_apply_provider(tmp_path, monkeypatch):
    import urllib.error
    import urllib.request

    import ai_ecosystem.interface.serve as serve
    from ai_ecosystem.core.config import AppConfig

    monkeypatch.setattr(serve, "list_ollama_models", lambda *_args: [])
    monkeypatch.setattr(serve, "check_ollama", lambda endpoint: {
        "provider": "ollama", "endpoint": endpoint, "models": ["test-model"], "connected": True,
    })
    for name in ("AI_ECO_MODEL_ENDPOINT", "MODEL_ENDPOINT", "AI_ECO_MODEL_NAME", "MODEL_NAME"):
        monkeypatch.delenv(name, raising=False)
    stack = serve.build_stack(
        AppConfig(db_path=str(tmp_path / "settings.db"), api_port=0),
        workspace=str(tmp_path / "workspace"), auth_token="test-credential",
    )
    server = stack.server.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(server.url + "/settings/model")
        assert denied.value.code == 401
        settings = OllamaSettings(model="test-model", temperature=0.2).model_dump()
        request = urllib.request.Request(
            server.url + "/settings/model", data=json.dumps(settings).encode(),
            headers={"Authorization": "Bearer test-credential", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as response:
            assert json.load(response) == settings
        assert stack.api.get_model_settings() == settings
        assert stack.providers[0].settings.temperature == 0.2
        assert stack.api.check_ollama()["models"] == ["test-model"]
        with pytest.raises(Exception, match="loopback"):
            stack.api.update_model_settings({**settings, "endpoint": "http://192.168.1.1:11434"})
        assert stack.api.get_model_settings() == settings
    finally:
        server.stop()
        stack.runtime.shutdown()


def test_runtime_reports_unreachable_ollama_as_connection_error(tmp_path):
    import socket

    from ai_ecosystem.core.runtime import AgentRuntime
    from ai_ecosystem.interface import ApiError, RuntimeAPI

    runtime = AgentRuntime(str(tmp_path / "offline.db"))
    try:
        with socket.socket() as unavailable:
            unavailable.bind(("127.0.0.1", 0))
            endpoint = f"http://127.0.0.1:{unavailable.getsockname()[1]}"
            with pytest.raises(ApiError, match="Ollama is unreachable") as failure:
                RuntimeAPI(runtime).check_ollama(endpoint)
            assert failure.value.code == "unavailable"
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("explicit_timeout, expected", [(None, 180.0), (12.0, 12.0), (0.2, 0.2)])
def test_local_inference_timeout_uses_settings_unless_request_is_explicit(monkeypatch, explicit_timeout, expected):
    captured = {}

    def respond(request, timeout):
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data)
        return _Response({"message": {"content": "OK"}})

    monkeypatch.setattr("ai_ecosystem.intelligence.models.ollama._open_local", respond)
    provider = OllamaModelProvider(OllamaSettings(model="test-model"))
    args = {} if explicit_timeout is None else {"timeout_s": explicit_timeout}
    provider.complete(ModelRequest(prompt="Hello", max_tokens=32, **args))
    assert captured["timeout"] == expected
    assert captured["body"]["think"] is False
    assert captured["body"]["options"]["num_predict"] == 32


def test_inference_timeout_has_actionable_diagnostic_and_does_not_retry(monkeypatch):
    import urllib.error
    from ai_ecosystem.core.errors.exceptions import ModelTimeoutError

    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr("ai_ecosystem.intelligence.models.ollama._open_local", fail)
    with pytest.raises(ModelTimeoutError, match="180.*Request timeout"):
        OllamaModelProvider(OllamaSettings(model="test-model")).complete(ModelRequest(prompt="Hello"))
    assert len(calls) == 1


def test_timeout_setting_and_thinking_are_validated():
    assert OllamaSettings(timeout_s=300, think=True).think is True
    with pytest.raises(ValueError):
        OllamaSettings(timeout_s=0)
    with pytest.raises(ValueError):
        OllamaSettings(timeout_s=601)
