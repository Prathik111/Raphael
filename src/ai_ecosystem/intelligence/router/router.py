"""Model routing with privacy-aware provider eligibility and failover."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ai_ecosystem.core.errors.exceptions import ModelError, NoSuitableModelError
from ai_ecosystem.security.data_policy import DataClass
from ai_ecosystem.intelligence.models.providers import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    request_structured,
)


class ProviderProfile(BaseModel):
    provider_id: str = ""
    local: bool = False
    trusted: bool = False
    cost_per_1k: float = 0.0
    latency_class: str = "standard"
    data_classes: set[DataClass] = Field(default_factory=set)
    retains_data: bool = True
    trains_on_data: bool = True
    region: str = ""


class RoutingRequirements(BaseModel):
    privacy: str = "any"
    data_classification: DataClass = DataClass.INTERNAL
    min_context: int = 0
    need_capabilities: list[str] = Field(default_factory=list)
    require_streaming: bool = False


class ModelRouter:
    """Select/fail over providers without bypassing privacy constraints."""

    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}
        self._profiles: dict[str, ProviderProfile] = {}

    def register(self, provider: ModelProvider, profile: ProviderProfile) -> None:
        # The provider object is the authoritative identity. Normalize the
        # profile to it rather than rejecting stale metadata during a provider
        # switch or test/restore path.
        self._providers[provider.provider_id] = provider
        self._profiles[provider.provider_id] = profile.model_copy(
            update={"provider_id": provider.provider_id}
        )

    @staticmethod
    def _privacy_allowed(profile: ProviderProfile, requirements: RoutingRequirements) -> bool:
        cls = requirements.data_classification
        if cls is DataClass.SECRET:
            return profile.local and not profile.trains_on_data
        if requirements.privacy == "local-only":
            return profile.local
        if cls is DataClass.SENSITIVE:
            return profile.local or (
                profile.trusted and cls in profile.data_classes and not profile.trains_on_data
            )
        if cls is DataClass.PERSONAL:
            return profile.local or (profile.trusted and cls in profile.data_classes)
        return not (cls not in profile.data_classes and not profile.local)

    def candidates(self, requirements: RoutingRequirements) -> list[ModelProvider]:
        ranked: list[tuple[float, int, str, ModelProvider]] = []
        for pid, provider in self._providers.items():
            profile = self._profiles.get(pid, ProviderProfile(provider_id=pid))
            if not self._privacy_allowed(profile, requirements):
                continue
            caps = provider.capabilities
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
        self, requirements: RoutingRequirements, request: ModelRequest, model_cls: type[BaseModel]
    ) -> BaseModel:
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
