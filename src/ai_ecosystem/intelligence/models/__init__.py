"""Public model-abstraction API."""

from ai_ecosystem.intelligence.models.http import (
    API_KEY_ENV,
    ENDPOINT_ENV,
    MODEL_ENV,
    HttpChatModelProvider,
    normalize_endpoint,
)
from ai_ecosystem.intelligence.models.ollama import (
    DEFAULT_ENDPOINT as OLLAMA_DEFAULT_ENDPOINT,
    OllamaModelProvider,
    OllamaSettings,
    check_ollama,
    list_ollama_models,
    normalize_ollama_endpoint,
)
from ai_ecosystem.intelligence.models.providers import (
    MockModelProvider,
    ModelCapabilities,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    ModelToolDefinition,
    extract_json_block,
    request_structured,
)

__all__ = [
    "API_KEY_ENV",
    "ENDPOINT_ENV",
    "MODEL_ENV",
    "HttpChatModelProvider",
    "OllamaModelProvider",
    "OllamaSettings",
    "OLLAMA_DEFAULT_ENDPOINT",
    "check_ollama",
    "list_ollama_models",
    "normalize_ollama_endpoint",
    "MockModelProvider",
    "ModelCapabilities",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "ModelToolCall",
    "ModelToolDefinition",
    "extract_json_block",
    "normalize_endpoint",
    "request_structured",
]
