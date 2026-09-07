"""Model routing (Gate 4): pick a provider from task requirements.

Deterministic and explainable: filter by hard constraints (privacy,
capabilities, context), then prefer cheapest and fastest. Fallback walks
the ranked list until one provider answers or all fail.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ai_ecosystem.core.errors.exceptions import ModelError, NoSuitableModelError
from ai_ecosystem.intelligence.models.providers import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
)


class ProviderProfile(BaseModel):
    """Static deployment facts the router may consider."""

    provider_id: str = ""
    local: bool = False
    cost_per_1k: float = 0.0
    latency_class: str = "standard"


class RoutingRequirements(BaseModel):
    """What the task needs from its model."""

    privacy: str = "any"  # "any" | "local-only"
    min_context: int = 0
    need_capabilities: list[str] = Field(default_factory=list)
    require_streaming: bool = False


class ModelRouter:
    """Selects and calls providers; the agent never picks one directly."""

    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}
        self._profiles: dict[str, ProviderProfile] = {}

    def register(self, provider: ModelProvider, profile: ProviderProfile) -> None:
        """Add (or replace) a provider and its deployment facts."""
        self._providers[provider.provider_id] = provider
        self._profiles[provider.provider_id] = profile

    def candidates(self, requirements: RoutingRequirements) -> list[ModelProvider]:
        """Providers satisfying the requirements, cheapest/fastest first."""
        ranked: list[tuple[float, str, ModelProvider]] = []
        for pid, provider in self._providers.items():
            profile = self._profiles.get(pid, ProviderProfile(provider_id=pid))
            caps = provider.capabilities
            if requirements.privacy == "local-only" and not profile.local:
                continue
            if caps.context_length < requirements.min_context:
                continue
            if requirements.require_streaming and not caps.streaming:
                continue
            missing = [
                name
                for name in requirements.need_capabilities
                if not getattr(caps, name, False)
            ]
            if missing:
                continue
            latency_rank = {"fast": 0, "standard": 1, "slow": 2}.get(
                profile.latency_class, 1
            )
            ranked.append((profile.cost_per_1k, latency_rank, pid, provider))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]))
        return [provider for _, _, _, provider in ranked]

    def select(self, requirements: RoutingRequirements) -> ModelProvider:
        """Best provider; raises NoSuitableModelError when none qualifies."""
        options = self.candidates(requirements)
        if not options:
            raise NoSuitableModelError(
                f"no provider satisfies {requirements.model_dump()}"
            )
        return options[0]

    def complete(
        self, requirements: RoutingRequirements, request: ModelRequest
    ) -> ModelResponse:
        """Complete via the best available provider, failing over in order."""
        options = self.candidates(requirements)
        if not options:
            raise NoSuitableModelError(
                f"no provider satisfies {requirements.model_dump()}"
            )
        last_error: ModelError | None = None
        for provider in options:
            try:
                return provider.complete(request)
            except ModelError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error
