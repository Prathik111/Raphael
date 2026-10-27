"""Regression coverage for local Ollama inference and conversation routing."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from ai_ecosystem.agent.planner.backend import ModelReasoningBackend
from ai_ecosystem.core.errors.exceptions import ModelMalformedError
from ai_ecosystem.intelligence.models.http import (
    HttpChatModelProvider,
    _parse_response,
    normalize_endpoint,
)
from ai_ecosystem.intelligence.models.providers import MockModelProvider, ModelResponse


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("http://127.0.0.1:11434", "http://127.0.0.1:11434/v1/chat/completions"),
        ("http://127.0.0.1:11434/v1", "http://127.0.0.1:11434/v1/chat/completions"),
        ("http://127.0.0.1:11434/v1/chat/completions", "http://127.0.0.1:11434/v1/chat/completions"),
        ("http://127.0.0.1:8080/v1", "http://127.0.0.1:8080/v1/chat/completions"),
        ("https://api.openai.com/v1", "https://api.openai.com/v1/chat/completions"),
    ],
)
def test_openai_compatible_endpoint_normalization(endpoint: str, expected: str) -> None:
    assert normalize_endpoint(endpoint) == expected


def test_no_credentials_in_model_url() -> None:
    with pytest.raises(ValueError):
        normalize_endpoint("http://user:secret@127.0.0.1:11434/v1")


def test_truncated_inference_is_not_accepted_as_complete() -> None:
    with pytest.raises(ModelMalformedError):
        _parse_response(
            {"choices": [{"message": {"content": '{"goal": "partial"}'}, "finish_reason": "length"}]},
            "ollama", "qwen3.5:4b",
        )


def test_explanatory_questions_are_not_executable_intents() -> None:
    for goal in ("What is a repository?", "Explain how files work", "How do I debug Java?"):
        assert not ModelReasoningBackend._looks_action_oriented(goal)
    assert ModelReasoningBackend._looks_action_oriented("Create a new file")


def test_conversation_uses_single_inference() -> None:
    provider = MockModelProvider(handler=lambda _: ModelResponse(text="A VPC is a virtual private cloud."))
    planner = ModelReasoningBackend(provider)
    plan = planner.plan("What is a VPC?", ["agent.respond"])
    assert len(provider.calls) == 1
    assert plan.steps[0].tools == ["agent.respond"]


def test_http_provider_sends_ollama_compatible_request() -> None:
    from ai_ecosystem.intelligence.models.providers import ModelRequest

    requests = []

    class FakeHttpResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _limit):
            return json.dumps({
                "model": "qwen3.5:4b",
                "choices": [{"message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 2},
            }).encode()

    def fake_urlopen(request, timeout):
        requests.append(request)
        return FakeHttpResponse()

    provider = HttpChatModelProvider(endpoint="http://127.0.0.1:11434", model="qwen3.5:4b")
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        reply = provider.complete(ModelRequest(prompt="hello"))
    assert requests[0].full_url == "http://127.0.0.1:11434/v1/chat/completions"
    assert json.loads(requests[0].data)["model"] == "qwen3.5:4b"
    assert reply.text == "hello"
    assert reply.latency_s >= 0


def test_ollama_sse_delivers_incremental_deltas() -> None:
    import io
    from ai_ecosystem.intelligence.models.providers import ModelRequest

    chunks = [
        {"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "Hel"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "lo"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    wire = "".join("data: " + json.dumps(chunk) + "\\n\\n" for chunk in chunks)
    wire += "data: [DONE]\\n\\n"
    # Real SSE event separators use newlines, not the escaped representation.
    wire = wire.replace("\\n", "\n")

    class StreamResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()
            return False

    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return StreamResponse(wire.encode())

    provider = HttpChatModelProvider(endpoint="http://127.0.0.1:11434", model="qwen3.5:4b")
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert list(provider.stream(ModelRequest(prompt="greet"))) == ["Hel", "lo"]
    assert provider.capabilities.streaming
    assert json.loads(requests[0].data)["stream"] is True
    assert requests[0].get_header("Accept") == "text/event-stream"


@pytest.mark.parametrize(
    "event",
    [
        {"choices": [{"delta": {"content": "partial"}, "finish_reason": "length"}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0}]}, "finish_reason": None}]},
    ],
)
def test_stream_does_not_silently_accept_truncation_or_tool_calls(event) -> None:
    import io
    from ai_ecosystem.intelligence.models.providers import ModelRequest

    class StreamResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()
            return False

    data = ("data: " + json.dumps(event) + "\n\n").encode()
    provider = HttpChatModelProvider(endpoint="http://127.0.0.1:11434", model="qwen3.5:4b")
    with patch("urllib.request.urlopen", return_value=StreamResponse(data)):
        with pytest.raises(ModelMalformedError):
            list(provider.stream(ModelRequest(prompt="hello")))


def test_stream_rejects_interrupted_sse() -> None:
    import io
    from ai_ecosystem.intelligence.models.providers import ModelRequest

    class StreamResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()
            return False

    provider = HttpChatModelProvider(endpoint="http://127.0.0.1:11434", model="qwen3.5:4b")
    with patch("urllib.request.urlopen", return_value=StreamResponse(b'data: {"choices": [')):
        with pytest.raises(ModelMalformedError):
            list(provider.stream(ModelRequest(prompt="hello")))
