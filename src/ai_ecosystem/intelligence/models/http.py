"""OpenAI-compatible HTTP chat provider."""

from __future__ import annotations

import ipaddress
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

from ai_ecosystem.core.errors.exceptions import (
    ModelMalformedError,
    ModelTimeoutError,
    ModelUnavailableError,
)
from ai_ecosystem.core.secrets import SecretsProvider
from ai_ecosystem.intelligence.models.providers import (
    ModelCapabilities,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
)

ENDPOINT_ENV = "MODEL_ENDPOINT"
API_KEY_ENV = "MODEL_API_KEY"
MODEL_ENV = "MODEL_NAME"
DEFAULT_TIMEOUT_S = 60.0
MAX_BODY_BYTES = 4_000_000


def _is_local_or_private(host: str) -> bool:
    normalized = host.strip("[]").lower()
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def normalize_endpoint(raw: str, *, api_key: str = "") -> str:
    """Normalize an endpoint while allowing keyless private LAN model servers.

    Plain HTTP is only allowed to loopback/private/link-local addresses and
    only when no API key is configured. Public/non-private HTTP always fails.
    """
    cleaned = raw.strip().rstrip("/")
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("model endpoint must be an absolute HTTP(S) URL")
    host = parsed.hostname or ""
    local_http = parsed.scheme == "http" and _is_local_or_private(host)
    if parsed.scheme == "http" and (not local_http or api_key):
        raise ValueError(
            "HTTP model endpoints are only allowed on loopback/private LAN addresses "
            "without an API key; use HTTPS for remote authenticated providers"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("model endpoint must not contain credentials, query or fragment")
    if parsed.path.endswith("/chat/completions"):
        return cleaned
    # Ollama, LM Studio and llama.cpp expose the OpenAI compatibility API at /v1.
    if not parsed.path or parsed.path == "/":
        return f"{cleaned}/v1/chat/completions"
    if parsed.path in {"/v1", "/api/v1"}:
        return f"{cleaned}/chat/completions"
    return f"{cleaned}/chat/completions"


class HttpChatModelProvider(ModelProvider):
    """OpenAI-compatible chat provider with normalized native tool calls."""

    def __init__(
        self,
        provider_id: str = "http-chat",
        capabilities: ModelCapabilities | None = None,
        endpoint: str = "",
        api_key: str = "",
        model: str = "",
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        super().__init__(
            provider_id,
            capabilities
            or ModelCapabilities(
                tool_calling=True,
                structured_output=True,
                reasoning=False,
                parallel_tool_calls=False,
                streaming=True,
            ),
        )
        if not endpoint:
            raise ValueError("endpoint is required")
        self._endpoint = normalize_endpoint(endpoint, api_key=api_key)
        self._api_key = api_key
        self._model = model or provider_id
        self._timeout = max(1.0, timeout_s)

    @classmethod
    def from_secrets(
        cls,
        secrets: SecretsProvider,
        provider_id: str = "http-chat",
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> HttpChatModelProvider | None:
        endpoint = secrets.get(ENDPOINT_ENV)
        if not endpoint:
            return None
        api_key = secrets.get(API_KEY_ENV) or ""
        return cls(
            provider_id=provider_id,
            endpoint=endpoint,
            api_key=api_key,
            model=secrets.get(MODEL_ENV) or provider_id,
            timeout_s=timeout_s,
        )

    @property
    def model(self) -> str:
        return self._model

    def complete(self, request: ModelRequest) -> ModelResponse:
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})
        payload: dict[str, object] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max(1, request.max_tokens),
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
            payload["tool_choice"] = request.tool_choice

        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "ai-ecosystem/0.1"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        http_request = urllib.request.Request(self._endpoint, data=body, method="POST", headers=headers)
        timeout = min(self._timeout, max(1.0, request.timeout_s))
        started = time.monotonic()
        try:
            with urllib.request.urlopen(http_request, timeout=timeout) as response:
                raw = response.read(MAX_BODY_BYTES + 1)
        except TimeoutError as exc:
            raise ModelTimeoutError(f"provider {self.provider_id!r} timed out") from exc
        except urllib.error.HTTPError as exc:
            raise ModelUnavailableError(
                f"provider {self.provider_id!r} refused the request (HTTP {exc.code})"
            ) from exc
        except OSError as exc:
            raise ModelUnavailableError(f"provider {self.provider_id!r} unreachable") from exc
        if len(raw) > MAX_BODY_BYTES:
            raise ModelMalformedError(f"provider {self.provider_id!r} returned an oversize body")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ModelMalformedError(f"provider {self.provider_id!r} returned invalid JSON") from exc
        parsed = _parse_response(decoded, self.provider_id, self._model)
        parsed.latency_s = time.monotonic() - started
        return parsed


    def stream(self, request: ModelRequest) -> Iterator[str]:
        """Yield OpenAI-compatible SSE content deltas as the provider emits them.

        Streaming exposes only display text; streamed tool calls are rejected,
        never executed or silently discarded. The agent's existing structured
        planner continues using complete() and its validation boundary.
        """
        if not self.capabilities.streaming:
            raise ModelUnavailableError(
                f"provider {self.provider_id!r} does not support streaming"
            )
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max(1, request.max_tokens),
            "stream": True,
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
            payload["tool_choice"] = request.tool_choice
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "ai-ecosystem/0.1",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        http_request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        timeout = min(self._timeout, max(1.0, request.timeout_s))
        try:
            with urllib.request.urlopen(http_request, timeout=timeout) as response:
                received = 0
                data_lines: list[str] = []
                finished = False
                saw_end = False
                while True:
                    raw_line = response.readline(MAX_BODY_BYTES + 1)
                    if not raw_line:
                        break
                    received += len(raw_line)
                    if received > MAX_BODY_BYTES or len(raw_line) > MAX_BODY_BYTES:
                        raise ModelMalformedError("model stream exceeded size limit")
                    try:
                        line = raw_line.decode("utf-8").rstrip("\r\n")
                    except UnicodeDecodeError as exc:
                        raise ModelMalformedError("invalid UTF-8 in model stream") from exc
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip(" "))
                    elif line == "":
                        if not data_lines:
                            continue
                        event_data = "\n".join(data_lines)
                        data_lines = []
                        if event_data == "[DONE]":
                            saw_end = True
                            break
                        try:
                            event = json.loads(event_data)
                        except ValueError as exc:
                            raise ModelMalformedError("invalid JSON SSE event") from exc
                        if not isinstance(event, dict):
                            raise ModelMalformedError("malformed SSE event")
                        if "error" in event:
                            raise ModelUnavailableError("model stream returned an error")
                        choices = event.get("choices")
                        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                            raise ModelMalformedError("model stream event has no valid choice")
                        choice = choices[0]
                        delta = choice.get("delta") or {}
                        if not isinstance(delta, dict):
                            raise ModelMalformedError("invalid streaming delta")
                        if delta.get("tool_calls") or delta.get("function_call"):
                            raise ModelMalformedError(
                                "streamed tool calls require structured execution; use complete()"
                            )
                        piece = delta.get("content")
                        if piece is not None:
                            if not isinstance(piece, str):
                                raise ModelMalformedError("invalid streamed content")
                            if piece:
                                yield piece
                        reason = choice.get("finish_reason")
                        if reason == "length":
                            raise ModelMalformedError("model stream truncated at output token limit")
                        if reason:
                            finished = True
                if not (saw_end or finished):
                    raise ModelMalformedError("model stream ended without completion")
        except TimeoutError as exc:
            raise ModelTimeoutError("model stream timed out") from exc
        except urllib.error.HTTPError as exc:
            raise ModelUnavailableError(
                f"model stream refused the request (HTTP {exc.code})"
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ModelUnavailableError("model streaming connection failed") from exc



def _parse_response(decoded: object, provider_id: str, default_model: str) -> ModelResponse:
    if not isinstance(decoded, dict):
        raise ModelMalformedError("chat response is not a JSON object")
    choices = decoded.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelMalformedError("chat response has no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ModelMalformedError("chat response choice is malformed")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ModelMalformedError("chat response message is malformed")

    raw_content = message.get("content", "")
    text = ""
    if raw_content is None:
        text = ""
    elif isinstance(raw_content, str):
        text = raw_content
    elif isinstance(raw_content, list):
        parts = [block.get("text", "") for block in raw_content if isinstance(block, dict)]
        text = "".join(part for part in parts if isinstance(part, str))
    else:
        raise ModelMalformedError("chat response content is malformed")

    tool_calls: list[ModelToolCall] = []
    raw_tool_calls = message.get("tool_calls") or []
    if not isinstance(raw_tool_calls, list):
        raise ModelMalformedError("chat response tool_calls is malformed")
    for index, raw_call in enumerate(raw_tool_calls):
        if not isinstance(raw_call, dict):
            raise ModelMalformedError("chat response contains malformed tool call")
        function = raw_call.get("function")
        if not isinstance(function, dict):
            raise ModelMalformedError("tool call function is malformed")
        name = function.get("name")
        arguments = function.get("arguments", "{}")
        if not isinstance(name, str) or not name:
            raise ModelMalformedError("tool call has no function name")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError as exc:
                raise ModelMalformedError("tool call arguments are not valid JSON") from exc
        if not isinstance(arguments, dict):
            raise ModelMalformedError("tool call arguments must be a JSON object")
        tool_calls.append(
            ModelToolCall(
                id=str(raw_call.get("id") or f"call-{index}"),
                name=name,
                arguments=arguments,
            )
        )

    usage: dict[str, Any] = {}
    usage_raw = decoded.get("usage")
    if isinstance(usage_raw, dict):
        usage = {str(key): value for key, value in usage_raw.items()}
    finish_reason = first.get("finish_reason") or ""
    if finish_reason == "length" and not tool_calls:
        raise ModelMalformedError(
            f"provider {provider_id!r} truncated the response at the output token limit"
        )
    if not text.strip() and not tool_calls:
        raise ModelMalformedError(
            f"provider {provider_id!r} returned empty content and no tool calls"
        )
    return ModelResponse(
        text=text,
        tool_calls=tool_calls,
        model=decoded.get("model", default_model)
        if isinstance(decoded.get("model"), str)
        else default_model,
        input_tokens=int(usage.get("prompt_tokens", 0) or 0),
        output_tokens=int(usage.get("completion_tokens", 0) or 0),
        finish_reason=finish_reason,
    )
