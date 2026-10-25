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
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(
            {"models": [{"name": "z-model:latest"}, {"name": "a-model:7b"}]}
        ),
    )
    assert list_ollama_models() == ["a-model:7b", "z-model:latest"]


def test_list_ollama_models_reports_unreachable_server(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fail)
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

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
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
