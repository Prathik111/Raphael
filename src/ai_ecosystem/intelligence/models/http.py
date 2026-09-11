"""HTTP chat-completions model provider (OpenAI-compatible, stdlib only)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

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
)

ENDPOINT_ENV = "MODEL_ENDPOINT"
API_KEY_ENV = "MODEL_API_KEY"
MODEL_ENV = "MODEL_NAME"
DEFAULT_TIMEOUT_S = 60.0
MAX_BODY_BYTES = 4_000_000


def normalize_endpoint(raw: str) -> str:
    """Normalize an OpenAI-compatible endpoint and reject unsafe transports."""
    cleaned = raw.strip().rstrip("/")
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("model endpoint must be an absolute HTTP(S) URL")
    host = (parsed.hostname or "").lower()
    is_loopback = host in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not is_loopback:
        raise ValueError("model endpoint must use HTTPS unless it targets localhost")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    return f"{cleaned}/chat/completions"


class HttpChatModelProvider(ModelProvider):
    """OpenAI-compatible chat provider over stdlib urllib.

    HTTPS is mandatory for non-loopback endpoints so API credentials cannot
    be intentionally or accidentally sent over plaintext transport.

    The key is optional: local providers (Ollama, LM Studio, llama.cpp
    server at e.g. http://127.0.0.1:11434/v1 with any model name) need
    no authentication, and none is sent then.
    """

    def __init__(
        self,
        provider_id: str = "http-chat",
        capabilities: Optional[ModelCapabilities] = None,
        endpoint: str = "",
        api_key: str = "",
        model: str = "",
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        super().__init__(provider_id, capabilities or ModelCapabilities(
            tool_calling=True, structured_output=True, reasoning=True))
        if not endpoint:
            raise ValueError("endpoint is required")
        self._endpoint = normalize_endpoint(endpoint)
        self._api_key = api_key
        self._model = model or provider_id
        self._timeout = max(1.0, timeout_s)

    @classmethod
    def from_secrets(cls, secrets: SecretsProvider, provider_id: str = "http-chat",
                     timeout_s: float = DEFAULT_TIMEOUT_S) -> Optional["HttpChatModelProvider"]:
        endpoint = secrets.get(ENDPOINT_ENV)
        if not endpoint:
            return None
        return cls(provider_id=provider_id, endpoint=endpoint,
                   api_key=secrets.get(API_KEY_ENV) or "",
                   model=secrets.get(MODEL_ENV) or provider_id, timeout_s=timeout_s)

    @property
    def model(self) -> str:
        return self._model

    def complete(self, request: ModelRequest) -> ModelResponse:
        payload = {
            "model": self._model,
            "messages": [
                *([{"role": "system", "content": request.system}] if request.system else []),
                {"role": "user", "content": request.prompt},
            ],
            "max_tokens": max(1, request.max_tokens),
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ai-ecosystem/0.1",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        http_request = urllib.request.Request(
            self._endpoint, data=body, method="POST", headers=headers)
        timeout = min(self._timeout, max(1.0, request.timeout_s))
        try:
            with urllib.request.urlopen(http_request, timeout=timeout) as response:
                raw = response.read()
        except TimeoutError as exc:
            raise ModelTimeoutError(f"provider {self.provider_id!r} timed out") from exc
        except urllib.error.HTTPError as exc:
            raise ModelUnavailableError(
                f"provider {self.provider_id!r} refused the request (HTTP {exc.code})") from exc
        except OSError as exc:
            raise ModelUnavailableError(f"provider {self.provider_id!r} unreachable") from exc
        if len(raw) > MAX_BODY_BYTES:
            raise ModelMalformedError(f"provider {self.provider_id!r} returned an oversize body")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ModelMalformedError(f"provider {self.provider_id!r} returned invalid JSON") from exc
        text = _extract_text(decoded)
        usage = decoded.get("usage", {}) if isinstance(decoded, dict) else {}
        return ModelResponse(
            text=text,
            model=decoded.get("model", self._model) if isinstance(decoded, dict) else self._model,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
        )


def _extract_text(decoded: object) -> str:
    if not isinstance(decoded, dict):
        raise ModelMalformedError("chat response is not a JSON object")
    choices = decoded.get("choices")
    if not choices:
        raise ModelMalformedError("chat response has no choices")
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = message.get("content", "")
    if isinstance(content, list):
        content = "".join(block.get("text", "") for block in content if isinstance(block, dict))
    if not isinstance(content, str) or not content.strip():
        raise ModelMalformedError("chat response has empty content")
    return content
