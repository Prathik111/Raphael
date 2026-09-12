"""OCI as an interchangeable model backend (Gate 23).

The planner and router treat this like any provider: same request in,
same response out. Remote failures surface as ordinary ModelErrors so
existing fallback logic applies unchanged.
"""

from __future__ import annotations

from ai_ecosystem.cloud.providers import (
    CloudAuthError,
    CloudUnavailableError,
    OCIProvider,
)
from ai_ecosystem.core.errors.exceptions import (
    ModelError,
    ModelUnavailableError,
)
from ai_ecosystem.intelligence.models.providers import (
    ModelCapabilities,
    ModelProvider,
    ModelRequest,
    ModelResponse,
)


class OCIModelProvider(ModelProvider):
    """A router-registered model served by OCI (mock-backed in tests)."""

    def __init__(
        self,
        provider_id: str,
        oci: OCIProvider,
        model: str,
        capabilities: ModelCapabilities | None = None,
    ) -> None:
        super().__init__(
            provider_id,
            capabilities or ModelCapabilities(structured_output=True, context_length=128000),
        )
        self._oci = oci
        self._model = model

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Complete remotely; transport failures become ModelErrors."""
        try:
            text = self._oci.complete_remote(self._model, request.prompt)
        except (CloudUnavailableError, CloudAuthError) as exc:
            raise ModelUnavailableError(f"OCI model {self._model!r} unavailable: {exc}") from exc
        except ModelError:
            raise
        except Exception as exc:  # noqa: BLE001 -- normalize foreign errors
            raise ModelUnavailableError(f"OCI model {self._model!r} failed") from exc
        return ModelResponse(text=text, model=self.provider_id)
