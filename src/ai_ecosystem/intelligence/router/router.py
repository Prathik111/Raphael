"""Model routing: deterministic selection plus failover for all request types."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ai_ecosystem.core.errors.exceptions import ModelError, NoSuitableModelError
from ai_ecosystem.intelligence.models.providers import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    request_structured,
)


class ProviderProfile(BaseModel):
    provider_id: str = ""
    local: bool = False
    cost_per_1k: float = 0.0
    latency_class: str = "standard"


class RoutingRequirements(BaseModel):
    privacy: str = "any"
    min_context: int = 0
    need_capabilities: list[str] = Field(default_factory=list)
    require_streaming: bool = False


class ModelRouter:
    """Select and fail over providers; callers never bypass routing policy."""

    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}
        self._profiles: dict[str, ProviderProfile] = {}

    def register(self, provider: ModelProvider, profile: ProviderProfile) -> None:
        self._providers[provider.provider_id] = provider
        self._profiles[provider.provider_id] = profile

    def candidates(self, requirements: RoutingRequirements) -> list[ModelProvider]:
        ranked: list[tuple[float, int, str, ModelProvider]] = []
        for pid, provider in self._providers.items():
            profile = self._profiles.get(pid, ProviderProfile(provider_id=pid))
            caps = provider.capabilities
            if requirements.privacy == "local-only" and not profile.local:
                continue
            if caps.context_length < requirements.min_context:
                continue
            if requirements.require_streaming and not caps.streaming:
                continue
            if any(not getattr(caps, name, False) for name in requirements.need_capabilities):
                continue
            latency_rank = {"fast": 0, "standard": 1, "slow": 2}.get(profile.latency_class, 1)
            ranked.append((profile.cost_per_1k, latency_rank, pid, provider))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]))
        return [provider for _, _, _, provider in ranked]

    def select(self, requirements: RoutingRequirements) -> ModelProvider:
        options = self.candidates(requirements)
        if not options:
            raise NoSuitableModelError(f"no provider satisfies {requirements.model_dump()}")
        return options[0]

    def complete(self, requirements: RoutingRequirements, request: ModelRequest) -> ModelResponse:
        options = self.candidates(requirements)
        if not options:
            raise NoSuitableModelError(f"no provider satisfies {requirements.model_dump()}")
        last_error: ModelError | None = None
        for provider in options:
            try:
                return provider.complete(request)
            except ModelError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    def request_structured(
        self,
        requirements: RoutingRequirements,
        request: ModelRequest,
        model_cls: type[BaseModel],
    ) -> BaseModel:
        """Structured request with the same provider failover as complete()."""
        options = self.candidates(requirements)
        if not options:
            raise NoSuitableModelError(f"no provider satisfies {requirements.model_dump()}")
        last_error: ModelError | None = None
        for provider in options:
            try:
                return request_structured(provider, request, model_cls)
            except ModelError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error
