"""Gate 4: model abstraction, routing, failure handling (all mocked)."""

import pytest

from ai_ecosystem.core.errors import (
    ModelMalformedError,
    ModelTimeoutError,
    ModelUnavailableError,
    NoSuitableModelError,
)
from ai_ecosystem.core.models import Plan
from ai_ecosystem.intelligence import (
    MockModelProvider,
    ModelCapabilities,
    ModelRequest,
    ModelResponse,
    ModelRouter,
    ProviderProfile,
    RoutingRequirements,
    request_structured,
)


def _router() -> ModelRouter:
    router = ModelRouter()
    router.register(
        MockModelProvider(
            "local",
            ModelCapabilities(structured_output=True, context_length=8192),
            handler=lambda req: ModelResponse(text="local-answer"),
        ),
        ProviderProfile(provider_id="local", local=True, cost_per_1k=0.0),
    )
    router.register(
        MockModelProvider(
            "cloud",
            ModelCapabilities(structured_output=True, context_length=128000),
            handler=lambda req: ModelResponse(text="cloud-answer"),
        ),
        ProviderProfile(provider_id="cloud", local=False, cost_per_1k=1.0),
    )
    return router


def test_provider_records_calls():
    provider = MockModelProvider("m")
    provider.complete(ModelRequest(prompt="hi"))
    assert len(provider.calls) == 1


def test_model_unavailable_propagates():
    def fail(req):
        raise ModelUnavailableError("down")

    provider = MockModelProvider("m", handler=fail)
    with pytest.raises(ModelUnavailableError):
        provider.complete(ModelRequest(prompt="hi"))


def test_model_timeout_propagates():
    def slow(req):
        raise ModelTimeoutError("too slow")

    with pytest.raises(ModelTimeoutError):
        MockModelProvider("m", handler=slow).complete(ModelRequest(prompt="hi"))


def test_malformed_structured_output_rejected():
    provider = MockModelProvider("m", handler=lambda req: ModelResponse(text="not json{{"))
    with pytest.raises(ModelMalformedError):
        request_structured(provider, ModelRequest(prompt="p"), Plan)


def test_valid_structured_output_accepted():
    provider = MockModelProvider(
        "m",
        handler=lambda req: ModelResponse(
            structured={"goal": "g", "steps": [], "final_verification": "v"}
        ),
    )
    plan = request_structured(provider, ModelRequest(prompt="p"), Plan)
    assert plan.goal == "g"


def test_router_prefers_local_and_cheap():
    router = _router()
    selected = router.select(RoutingRequirements())
    assert selected.provider_id == "local"


def test_router_enforces_local_only_privacy():
    router = _router()
    selected = router.select(RoutingRequirements(privacy="local-only"))
    assert selected.provider_id == "local"


def test_router_no_match_raises():
    router = _router()
    with pytest.raises(NoSuitableModelError):
        router.select(RoutingRequirements(need_capabilities=["vision"]))


def test_router_fallback_on_provider_failure():
    router = ModelRouter()

    def fail(req):
        raise ModelUnavailableError("primary down")

    router.register(
        MockModelProvider("primary", ModelCapabilities(), handler=fail),
        ProviderProfile(provider_id="primary", local=True, cost_per_1k=0.0),
    )
    router.register(
        MockModelProvider(
            "backup",
            ModelCapabilities(),
            handler=lambda req: ModelResponse(text="backup-answer"),
        ),
        ProviderProfile(provider_id="backup", local=True, cost_per_1k=5.0),
    )
    response = router.complete(RoutingRequirements(), ModelRequest(prompt="p"))
    assert response.text == "backup-answer"


def test_router_raises_last_error_when_all_fail():
    router = ModelRouter()

    def fail(req):
        raise ModelUnavailableError("all down")

    router.register(
        MockModelProvider("only", ModelCapabilities(), handler=fail),
        ProviderProfile(provider_id="only", local=True),
    )
    with pytest.raises(ModelUnavailableError):
        router.complete(RoutingRequirements(), ModelRequest(prompt="p"))


def test_streaming_round_trip():
    provider = MockModelProvider(
        "s",
        ModelCapabilities(streaming=True),
        handler=lambda req: ModelResponse(text="0123456789abcdef"),
    )
    assert "".join(provider.stream(ModelRequest(prompt="p"))) == "0123456789abcdef"


def test_streaming_unsupported_raises():
    provider = MockModelProvider("s", ModelCapabilities(streaming=False))
    with pytest.raises(ModelUnavailableError):
        list(provider.stream(ModelRequest(prompt="p")))


def test_models_are_interchangeable_behind_router():
    router = _router()
    first = router.complete(RoutingRequirements(), ModelRequest(prompt="p"))
    assert first.text in ("local-answer", "cloud-answer")


def _http_provider(monkeypatch, payload, status=200):
    """HttpChatModelProvider with urlopen stubbed to return payload."""
    import io
    import json as _json

    from ai_ecosystem.intelligence import HttpChatModelProvider

    class FakeResponse:
        def __init__(self, body, code=status):
            self._body = body
            self.code = code

        def read(self, size=-1):
            return self._body if size < 0 else self._body[:size]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["timeout"] = timeout
        body = _json.dumps(payload).encode()
        if status != 200:
            import urllib.error

            raise urllib.error.HTTPError(request.full_url, status, "err", {}, io.BytesIO(body))
        return FakeResponse(body)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = HttpChatModelProvider(
        endpoint="https://llm.test/v1", api_key="test-key-123", model="test-model"
    )
    return provider, seen


def test_http_provider_posts_chat_completions(monkeypatch):
    from ai_ecosystem.intelligence import ModelRequest

    provider, seen = _http_provider(
        monkeypatch,
        {
            "model": "test-model",
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        },
    )
    response = provider.complete(ModelRequest(prompt="hi", system="sys"))
    assert response.text == "hello"
    assert seen["url"] == "https://llm.test/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key-123"
    assert response.input_tokens == 3 and response.output_tokens == 1


def test_http_provider_key_never_in_errors(monkeypatch):
    import urllib.error

    from ai_ecosystem.core.errors import ModelUnavailableError
    from ai_ecosystem.intelligence import HttpChatModelProvider, ModelRequest

    def boom(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "denied", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", boom)
    provider = HttpChatModelProvider(endpoint="https://llm.test/v1", api_key="super-secret-key")
    with pytest.raises(ModelUnavailableError) as excinfo:
        provider.complete(ModelRequest(prompt="hi"))
    assert "super-secret-key" not in str(excinfo.value)


def test_http_provider_empty_content_rejected(monkeypatch):
    from ai_ecosystem.core.errors import ModelMalformedError
    from ai_ecosystem.intelligence import ModelRequest

    provider, _ = _http_provider(monkeypatch, {"choices": []})
    with pytest.raises(ModelMalformedError):
        provider.complete(ModelRequest(prompt="hi"))


def test_http_provider_from_secrets_absent_returns_none():
    from ai_ecosystem.core.secrets import DictSecretsProvider
    from ai_ecosystem.intelligence import HttpChatModelProvider

    assert HttpChatModelProvider.from_secrets(DictSecretsProvider({})) is None


def test_normalize_endpoint_appends_chat_path():
    from ai_ecosystem.intelligence import normalize_endpoint

    assert normalize_endpoint("https://h/v1") == "https://h/v1/chat/completions"
    assert normalize_endpoint("https://h/v1/chat/completions") == "https://h/v1/chat/completions"


def test_extract_json_block_prefers_full_text():
    from ai_ecosystem.intelligence import extract_json_block

    assert extract_json_block('{"a": 1}') == '{"a": 1}'


def test_extract_json_block_strips_fences_and_prose():
    from ai_ecosystem.intelligence import extract_json_block

    fenced = 'Here you go:\n```json\n{"a": 1}\n```\nhope it helps'
    assert extract_json_block(fenced) == '{"a": 1}'
    assert extract_json_block('Sure: {"a": {"b": "x} tricky"}} done') == '{"a": {"b": "x} tricky"}}'


def test_request_structured_accepts_fenced_json():
    from ai_ecosystem.core.models import Plan
    from ai_ecosystem.intelligence import request_structured

    plan_json = (
        '{"goal": "g", "steps": [{"id": "s1", "description": "d", '
        '"dependencies": [], "tools": [], "risk": "LOW", '
        '"verification": "v", "completion_criteria": "c"}], '
        '"final_verification": "v"}'
    )
    provider = MockModelProvider(
        "m", handler=lambda req: ModelResponse(text=f"```json\n{plan_json}\n```")
    )
    assert request_structured(provider, ModelRequest(prompt="p"), Plan).goal == "g"


def test_planner_repairs_rejected_draft():
    from ai_ecosystem.agent.planner.backend import ModelReasoningBackend

    good = (
        '{"goal": "g", "steps": [{"id": "s1", "description": "d", '
        '"dependencies": [], "tools": ["work"], "risk": "LOW", '
        '"verification": "v", "completion_criteria": "c"}], '
        '"final_verification": "v"}'
    )
    calls = []

    def handle(req):
        calls.append(req.prompt)
        if len(calls) == 1:
            return ModelResponse(text='{"goal": "g"}')
        return ModelResponse(text=good)

    backend = ModelReasoningBackend(MockModelProvider("m", handler=handle))
    plan = backend.plan("g", ["work"])
    assert [s.id for s in plan.steps] == ["s1"]
    assert len(calls) == 2
    assert "rejected" in calls[1]


def test_planner_raises_after_exhausted_repairs():
    from ai_ecosystem.agent.planner.backend import ModelReasoningBackend
    from ai_ecosystem.core.errors import ModelMalformedError

    backend = ModelReasoningBackend(
        MockModelProvider("m", handler=lambda req: ModelResponse(text="not json at all")),
        max_repair_attempts=1,
    )
    with pytest.raises(ModelMalformedError):
        backend.plan("g", ["work"])
