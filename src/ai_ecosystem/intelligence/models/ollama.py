"""Native Ollama chat provider and validated model-generation settings."""

from __future__ import annotations

import ipaddress
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ai_ecosystem.core.errors.exceptions import (
    ModelMalformedError,
    ModelTimeoutError,
    ModelUnavailableError,
)
from ai_ecosystem.intelligence.models.providers import (
    ModelCapabilities,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
)

MAX_BODY_BYTES = 4_000_000
DEFAULT_ENDPOINT = "http://127.0.0.1:11434"


def normalize_ollama_endpoint(raw: str) -> str:
    """Accept only a loopback Ollama server URL; return its API root."""
    cleaned = raw.strip().rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(cleaned)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        raise ValueError("Ollama endpoint is not a valid URL") from None

    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise ValueError("Ollama endpoint must be an absolute local HTTP(S) URL")
    if parsed.query or parsed.fragment:
        raise ValueError("Ollama endpoint must not contain a query or fragment")

    if host == "localhost":
        local_host = True
    else:
        try:
            address = ipaddress.ip_address(host)
            local_host = address.is_loopback
        except ValueError:
            local_host = False
    if not local_host:
        raise ValueError("Ollama endpoint must use localhost or a loopback IP address")

    path = parsed.path.rstrip("/")
    if path not in {"", "/", "/v1"}:
        raise ValueError("Enter the Ollama server base URL, not an API path")

    netloc = parsed.netloc
    if port is None:
        netloc = f"[{host}]" if ":" in host else host
    return f"{parsed.scheme}://{netloc}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Ollama redirects are disabled", headers, fp)


def _open_local(request: urllib.request.Request, timeout: float):
    # A local provider must not use environment proxies or follow redirects.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    return opener.open(request, timeout=timeout)


class OllamaSettings(BaseModel):
    """Provider and generation settings exposed by the desktop Settings page."""

    provider: Literal["ollama"] = "ollama"
    endpoint: str = DEFAULT_ENDPOINT
    model: str = ""
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, gt=0.0, le=1.0)
    top_k: int = Field(default=40, ge=0, le=1000)
    repeat_penalty: float = Field(default=1.1, ge=0.1, le=3.0)
    num_ctx: int = Field(default=8192, ge=256, le=262144)
    num_predict: int = Field(default=1024, ge=1, le=32768)
    timeout_s: float = Field(default=180.0, ge=1.0, le=600.0)
    think: bool = False

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        return normalize_ollama_endpoint(value)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        value = value.strip()
        if len(value) > 200:
            raise ValueError("Ollama model name is too long")
        if any(ord(char) < 32 for char in value):
            raise ValueError("Ollama model name contains invalid characters")
        return value


def list_ollama_models(endpoint: str = DEFAULT_ENDPOINT, timeout_s: float = 2.0) -> list[str]:
    """List installed model tags without sending prompts or credentials."""
    base = normalize_ollama_endpoint(endpoint)
    request = urllib.request.Request(
        f"{base}/api/tags",
        headers={"Accept": "application/json", "User-Agent": "Raphael/0.1"},
        method="GET",
    )
    try:
        with _open_local(request, timeout=max(0.2, min(timeout_s, 10.0))) as response:
            raw = response.read(1_000_000 + 1)
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        raise ModelUnavailableError(
            f"Ollama is unreachable at {base}; check that Ollama is running and the endpoint is correct"
        ) from exc
    if len(raw) > 1_000_000:
        raise ModelMalformedError("Ollama returned an oversized model list")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ModelMalformedError("Ollama returned invalid JSON while listing models") from exc
    if not isinstance(decoded, dict) or not isinstance(decoded.get("models"), list):
        raise ModelMalformedError("Ollama model list response has an unexpected shape")
    names: list[str] = []
    for item in decoded["models"]:
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"]:
            names.append(item["name"])
    return sorted(set(names), key=str.casefold)


def check_ollama(endpoint: str = DEFAULT_ENDPOINT, timeout_s: float = 2.0) -> dict[str, Any]:
    """Return a minimal connection result and installed model list."""
    base = normalize_ollama_endpoint(endpoint)
    models = list_ollama_models(base, timeout_s=timeout_s)
    return {"provider": "ollama", "endpoint": base, "connected": True, "models": models}


class OllamaModelProvider(ModelProvider):
    """Ollama's native /api/chat provider with model-specific runtime options."""

    def __init__(self, settings: OllamaSettings | dict[str, Any] | None = None) -> None:
        self.settings = (
            settings if isinstance(settings, OllamaSettings) else OllamaSettings.model_validate(settings or {})
        )
        super().__init__(
            "ollama",
            ModelCapabilities(
                tool_calling=True,
                structured_output=True,
                reasoning=True,
                streaming=False,
                parallel_tool_calls=True,
                context_length=self.settings.num_ctx,
            ),
        )
        self._endpoint = self.settings.endpoint
        self._model = self.settings.model

    @property
    def model(self) -> str:
        return self._model

    def list_models(self, timeout_s: float = 2.0) -> list[str]:
        return list_ollama_models(self._endpoint, timeout_s=timeout_s)

    def complete(self, request: ModelRequest) -> ModelResponse:
        model = self._model
        if not model:
            available = self.list_models()
            if not available:
                raise ModelUnavailableError(
                    "No Ollama model is installed. Pull a model in Ollama or select one in Raphael Settings."
                )
            model = available[0]

        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": self.settings.think,
            "options": {
                "temperature": self.settings.temperature,
                "top_p": self.settings.top_p,
                "top_k": self.settings.top_k,
                "repeat_penalty": self.settings.repeat_penalty,
                "num_ctx": self.settings.num_ctx,
                "num_predict": min(self.settings.num_predict, max(1, request.max_tokens)),
            },
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in request.tools
            ]

        body = json.dumps(payload).encode("utf-8")
        http_request = urllib.request.Request(
            f"{self._endpoint}/api/chat",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Raphael/0.1",
            },
        )
        # Local model loading can exceed the generic 30-second request default.
        # An explicit caller timeout remains an upper bound on the provider budget.
        timeout = self.settings.timeout_s
        if "timeout_s" in request.model_fields_set:
            if not math.isfinite(request.timeout_s) or request.timeout_s <= 0:
                raise ModelMalformedError("Model request timeout must be positive and finite")
            timeout = min(timeout, request.timeout_s)
        timeout_message = (
            f"Ollama model {model!r} timed out after {timeout:g} seconds. "
            "Increase Request timeout in Settings, disable thinking, or reduce context/output length. "
            "Ollama may still be finishing the request."
        )
        started = time.monotonic()
        try:
            with _open_local(http_request, timeout=timeout) as response:
                raw = response.read(MAX_BODY_BYTES + 1)
        except TimeoutError as exc:
            raise ModelTimeoutError(timeout_message) from exc
        except urllib.error.HTTPError as exc:
            raise ModelUnavailableError(
                f"Ollama rejected the request for model {model!r} (HTTP {exc.code}); "
                "check that the model is installed and supports chat"
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError):
                raise ModelTimeoutError(timeout_message) from exc
            raise ModelUnavailableError(
                f"Ollama is unreachable at {self._endpoint}; check that Ollama is running"
            ) from exc

        if len(raw) > MAX_BODY_BYTES:
            raise ModelMalformedError("Ollama returned an oversized response")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ModelMalformedError("Ollama returned invalid JSON") from exc
        if not isinstance(decoded, dict) or not isinstance(decoded.get("message"), dict):
            raise ModelMalformedError("Ollama chat response is malformed")

        message = decoded["message"]
        content = message.get("content", "")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise ModelMalformedError("Ollama message content is malformed")

        tool_calls: list[ModelToolCall] = []
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise ModelMalformedError("Ollama tool_calls is malformed")
        for index, raw_call in enumerate(raw_calls):
            if not isinstance(raw_call, dict) or not isinstance(raw_call.get("function"), dict):
                raise ModelMalformedError("Ollama returned a malformed tool call")
            function = raw_call["function"]
            name = function.get("name")
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError as exc:
                    raise ModelMalformedError("Ollama tool arguments are invalid JSON") from exc
            if not isinstance(name, str) or not name or not isinstance(arguments, dict):
                raise ModelMalformedError("Ollama tool call has invalid name or arguments")
            tool_calls.append(
                ModelToolCall(id=f"ollama-call-{index}", name=name, arguments=arguments)
            )

        if not content.strip() and not tool_calls:
            raise ModelMalformedError("Ollama returned empty content and no tool calls")
        return ModelResponse(
            text=content,
            tool_calls=tool_calls,
            model=str(decoded.get("model") or model),
            input_tokens=int(decoded.get("prompt_eval_count") or 0),
            output_tokens=int(decoded.get("eval_count") or 0),
            latency_s=time.monotonic() - started,
            finish_reason=str(decoded.get("done_reason") or ""),
        )
