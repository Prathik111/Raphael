"""Safe discovery of local OpenAI-compatible model servers.

Discovery is deliberately local-only. It never sends credentials or user data;
it only probes loopback endpoints for a model list. Explicit environment
configuration always wins over discovery.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from ai_ecosystem.core.secrets import EnvSecretsProvider
from ai_ecosystem.intelligence.models.http import HttpChatModelProvider

_DEFAULTS = (
    "http://127.0.0.1:11434/v1",  # Ollama OpenAI-compatible API
    "http://127.0.0.1:1234/v1",  # LM Studio
    "http://127.0.0.1:8080/v1",  # llama.cpp server
)


def _models(endpoint: str, timeout_s: float = 0.8) -> list[str]:
    url = endpoint.rstrip("/") + "/models"
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            decoded = json.loads(response.read(1_000_000).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, urllib.error.URLError):
        return []
    if not isinstance(decoded, dict) or not isinstance(decoded.get("data"), list):
        return []
    names: list[str] = []
    for item in decoded["data"]:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
            names.append(item["id"])
    return names


def discover_local_provider() -> HttpChatModelProvider | None:
    """Return the first explicitly configured or healthy local provider."""
    configured = HttpChatModelProvider.from_secrets(EnvSecretsProvider())
    if configured is not None:
        return configured

    candidates = tuple(
        value.strip().rstrip("/")
        for value in os.environ.get("AI_ECO_LOCAL_MODEL_ENDPOINTS", "").split(",")
        if value.strip()
    ) or _DEFAULTS
    for endpoint in candidates:
        names = _models(endpoint)
        if not names:
            continue
        try:
            return HttpChatModelProvider(
                provider_id=f"local-{endpoint.split('//', 1)[-1].split('/', 1)[0].replace(':', '-')}",
                endpoint=endpoint,
                api_key="",
                model=os.environ.get("AI_ECO_MODEL_NAME") or names[0],
            )
        except ValueError:
            continue
    return None
