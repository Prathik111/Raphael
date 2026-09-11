"""OpenAI-compatible HTTP chat provider."""

from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request

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
    if cleaned.endswith("/chat/completions"):
        return cleaned
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
                reasoning=True,
                parallel_tool_calls=True,
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
        http_request = urllib.request.Request(
            self._endpoint, data=body, method="POST", headers=headers
        )
        timeout = min(self._timeout, max(1.0, request.timeout_s))
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
            raise ModelMalformedError(
                f"provider {self.provider_id!r} returned invalid JSON"
            ) from exc
        return _parse_response(decoded, self.provider_id, self._model)


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

    usage = decoded.get("usage") if isinstance(decoded.get("usage"), dict) else {}
    finish_reason = first.get("finish_reason") or ""
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
