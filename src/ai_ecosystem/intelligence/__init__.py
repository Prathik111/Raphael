"""Public intelligence API (models + routing).

Research lives in ``ai_ecosystem.intelligence.research`` and is imported
from there directly: eagerly re-exporting it here would create an import
cycle (research -> recovery -> executor -> planner -> intelligence).
"""

from ai_ecosystem.intelligence.models import (
    API_KEY_ENV,
    ENDPOINT_ENV,
    MODEL_ENV,
    HttpChatModelProvider,
    MockModelProvider,
    ModelCapabilities,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    extract_json_block,
    normalize_endpoint,
    request_structured,
)
from ai_ecosystem.intelligence.router import (
    ModelRouter,
    ProviderProfile,
    RoutingRequirements,
)

__all__ = [
    "API_KEY_ENV",
    "ENDPOINT_ENV",
    "MODEL_ENV",
    "HttpChatModelProvider",
    "MockModelProvider",
    "ModelCapabilities",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "ModelRouter",
    "ProviderProfile",
    "RoutingRequirements",
    "extract_json_block",
    "normalize_endpoint",
    "request_structured",
]
