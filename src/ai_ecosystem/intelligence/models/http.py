"""HTTP chat-completions model provider (OpenAI-compatible, stdlib only).

The agent needs an API key + endpoint to run against a real model.
Both arrive via configuration/environment -- never via the frontend:

* ``AI_ECO_MODEL_ENDPOINT`` -- chat-completions URL
  (e.g. ``https://api.openai.com/v1/chat/completions``). A bare
  ``https://host/v1`` base is also accepted (``/chat/completions``
  is appended).
* ``AI_ECO_MODEL_API_KEY`` -- bearer credential (read once per
  process via SecretsProvider, never logged, never echoed).
* ``AI_ECO_MODEL_NAME`` -- model id sent in the payload.

Any OpenAI-compatible endpoint works (OpenAI, OCI Generative AI
gateway in OpenAI-compat mode, LiteLLM, Ollama with a key, ...).
The provider returns plain text; structured planning output is
parsed by :func:`request_structured` exactly like any other provider.
"""

from __future__ import annotations

import json
import urllib.error
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
)

ENDPOINT_ENV = "MODEL_ENDPOINT"
API_KEY_ENV = "MODEL_API_KEY"
MODEL_ENV = "MODEL_NAME"

DEFAULT_TIMEOUT_S = 60.0
MAX_BODY_BYTES = 4_000_000


def normalize_endpoint(raw: str) -> str:
    """Accept a full chat-completions URL or a ``/v1`` base URL."""
    cleaned = raw.strip().rstrip("/")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    return f"{cleaned}/chat/completions"


class HttpChatModelProvider(ModelProvider):
    """OpenAI-compatible chat provider over stdlib urllib.

    The key is held in memory only for request signing; it never
    appears in logs, errors, events, or prompts.
    """

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
                tool_calling=True, structured_output=True, reasoning=True
            ),
        )
        if not endpoint:
            raise ValueError("endpoint is required")
        if not api_key:
            raise ValueError("api_key is required")
        self._endpoint = normalize_endpoint(endpoint)
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
        """Build from secrets; None when endpoint/key are not configured."""
        endpoint = secrets.get(ENDPOINT_ENV)
        api_key = secrets.get(API_KEY_ENV)
        if not endpoint or not api_key:
            return None
        return cls(
            provider_id=provider_id,
            endpoint=endpoint,
            api_key=api_key,
            model=secrets.get(MODEL_ENV) or provider_id,
            timeout_s=timeout_s,
        )

    @property
    def model(self) -> str:
        """Configured model id (no credential content)."""
        return self._model

    def complete(self, request: ModelRequest) -> ModelResponse:
        """POST one chat request; map transport failures to ModelErrors."""
        payload = {
            "model": self._model,
            "messages": [
                *(
                    [{"role": "system", "content": request.system}]
                    if request.system
                    else []
                ),
                {"role": "user", "content": request.prompt},
            ],
            "max_tokens": max(1, request.max_tokens),
        }
        body = json.dumps(payload).encode("utf-8")
        http_request = urllib.request.Request(
            self._endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
                # Groq fronts its API with Cloudflare, which challenges
                # the default "Python-urllib" user agent (error 1010).
                "User-Agent": "ai-ecosystem/0.1",
            },
        )
        timeout = min(self._timeout, max(1.0, request.timeout_s))
        try:
            with urllib.request.urlopen(http_request, timeout=timeout) as response:
                raw = response.read()
        except TimeoutError as exc:
            raise ModelTimeoutError(f"provider {self.provider_id!r} timed out") from exc
        except urllib.error.HTTPError as exc:
            raise ModelUnavailableError(
                f"provider {self.provider_id!r} refused the request (HTTP {exc.code})"
            ) from exc
        except OSError as exc:
            raise ModelUnavailableError(
                f"provider {self.provider_id!r} unreachable"
            ) from exc
        if len(raw) > MAX_BODY_BYTES:
            raise ModelMalformedError(
                f"provider {self.provider_id!r} returned an oversize body"
            )
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ModelMalformedError(
                f"provider {self.provider_id!r} returned invalid JSON"
            ) from exc
        text = _extract_text(decoded)
        usage = decoded.get("usage", {}) if isinstance(decoded, dict) else {}
        return ModelResponse(
            text=text,
            model=decoded.get("model", self._model)
            if isinstance(decoded, dict)
            else self._model,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
        )


def _extract_text(decoded: object) -> str:
    """Pull the assistant text out of a chat-completions envelope."""
    if not isinstance(decoded, dict):
        raise ModelMalformedError("chat response is not a JSON object")
    choices = decoded.get("choices")
    if not choices:
        raise ModelMalformedError("chat response has no choices")
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = message.get("content", "")
    if isinstance(content, list):  # multipart content blocks
        content = "".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        )
    if not isinstance(content, str) or not content.strip():
        raise ModelMalformedError("chat response has empty content")
    return content
