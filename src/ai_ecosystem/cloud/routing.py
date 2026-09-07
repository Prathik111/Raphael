"""Provider-independent compute routing (Gate 26).

The router maps task requirements + operator policy to an ordered list
of targets. It never executes, never authorizes, and never lets cost
or privacy be decided implicitly: high-privacy work stays local or the
call fails loudly instead of leaking to a cloud.
"""

from __future__ import annotations

from typing import Callable, Optional

from pydantic import BaseModel, Field

from ai_ecosystem.core.errors.exceptions import AiEcosystemError, DomainValidationError


class RoutingError(AiEcosystemError):
    """Base class for routing failures (explicit, never silent)."""


class NoRouteError(RoutingError):
    """No provider satisfies the requirements under policy."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"no compute route: {reason}")


class PolicyRejectionError(RoutingError):
    """Policy forbids every otherwise-suitable target."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"compute policy rejects routing: {reason}")


class ComputeRequirements(BaseModel):
    """What a task needs from its execution home."""

    cpu: str = ""
    ram_gb: float = 0.0
    gpu: bool = False
    vram_gb: float = 0.0
    runtime: str = ""
    model: str = ""
    context_tokens: int = 0
    duration_s: float = 0.0
    network: bool = False
    privacy: str = "standard"  # standard | high
    max_cost: float = -1.0  # negative: no ceiling
    deadline_s: float = 0.0


class ProviderCapabilities(BaseModel):
    """What one provider offers (declared facts, not promises)."""

    name: str = ""
    local: bool = False
    gpu: bool = False
    vram_gb: float = 0.0
    ram_gb: float = 0.0
    runtimes: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    network: bool = True
    cost_per_hour: float = 0.0
    latency_class: str = "standard"  # fast | standard | slow
    reliability: float = 1.0


class ComputePolicy(BaseModel):
    """Operator routing policy (explicit allow-lists and ceilings)."""

    allowed_providers: list[str] = Field(default_factory=list)  # empty: all named below
    blocked_providers: list[str] = Field(default_factory=list)
    max_cost_per_hour: float = -1.0
    require_local_for_high_privacy: bool = True


class ComputeTarget(BaseModel):
    """One ranked routing answer with its justification."""

    provider: str = ""
    reason: str = ""
    estimated_cost: float = 0.0


class ComputeRouter:
    """Deterministic policy-checked routing over declared capabilities."""

    _LATENCY_RANK = {"fast": 0, "standard": 1, "slow": 2}

    def __init__(
        self,
        providers: Optional[dict[str, ProviderCapabilities]] = None,
        availability: Optional[Callable[[str], bool]] = None,
        policy: Optional[ComputePolicy] = None,
    ) -> None:
        self._providers = dict(providers or {})
        self._availability = availability or (lambda name: True)
        self._policy = policy or ComputePolicy()

    def register(self, capabilities: ProviderCapabilities) -> None:
        """Add or replace a provider's declared capabilities."""
        if not capabilities.name:
            raise DomainValidationError("provider name must not be empty")
        self._providers[capabilities.name] = capabilities

    def rank(self, requirements: ComputeRequirements) -> list[ComputeTarget]:
        """All suitable targets, cheapest/fastest/most-reliable first.

        High-privacy work only ever ranks local providers (when the
        policy requires it): cloud targets are filtered, not merely
        deprioritized, so a cheaper cloud can never win.
        """
        self._validate(requirements)
        local_only = (
            requirements.privacy == "high"
            and self._policy.require_local_for_high_privacy
        )
        candidates: list[tuple[float, int, float, str, str]] = []
        for name, caps in self._providers.items():
            if local_only and not caps.local:
                continue
            if not self._available(name, requirements, caps):
                continue
            if not self._capable(requirements, caps):
                continue
            if not self._permitted(name, caps, requirements):
                continue
            cost = caps.cost_per_hour * max(0.0, requirements.duration_s) / 3600.0
            candidates.append((
                cost,
                self._LATENCY_RANK.get(caps.latency_class, 2),
                -caps.reliability,
                name,
                self._reason(requirements, caps),
            ))
        candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
        return [ComputeTarget(provider=name, reason=reason, estimated_cost=cost)
                for cost, _, _, name, reason in candidates]

    def route(self, requirements: ComputeRequirements) -> ComputeTarget:
        """Best target, or an explicit error (never a silent default)."""
        ranked = self.rank(requirements)
        if not ranked:
            raise NoRouteError(self._why_empty(requirements))
        return ranked[0]

    def select_first_available(
        self, ranked: list[ComputeTarget],
        probe: Callable[[str], bool],
    ) -> ComputeTarget:
        """Walk ranked targets until one probes healthy (fallback)."""
        last = ""
        for target in ranked:
            try:
                if probe(target.provider):
                    return target
            except Exception as exc:  # noqa: BLE001 -- probe failure = skip
                last = str(exc)
                continue
            last = f"{target.provider} probe failed"
        raise NoRouteError(f"all providers failed probing ({last})")

    # -- filters -----------------------------------------------------------

    def _available(self, name: str, requirements: ComputeRequirements,
                   caps: ProviderCapabilities) -> bool:
        try:
            return bool(self._availability(name))
        except Exception:  # noqa: BLE001 -- availability errors mean down
            return False

    @staticmethod
    def _capable(requirements: ComputeRequirements,
                 caps: ProviderCapabilities) -> bool:
        if requirements.gpu and not caps.gpu:
            return False
        if requirements.vram_gb > caps.vram_gb:
            return False
        if requirements.ram_gb > caps.ram_gb:
            return False
        if requirements.runtime and requirements.runtime not in caps.runtimes:
            return False
        if requirements.model and requirements.model not in caps.models:
            return False
        if requirements.network and not caps.network:
            return False
        return True

    def _permitted(self, name: str, caps: ProviderCapabilities,
                   requirements: ComputeRequirements | None = None) -> bool:
        policy = self._policy
        if name in policy.blocked_providers:
            return False
        if policy.allowed_providers and name not in policy.allowed_providers:
            return False
        if policy.max_cost_per_hour >= 0 and caps.cost_per_hour > policy.max_cost_per_hour:
            return False
        if requirements is not None and requirements.max_cost >= 0:
            cost = caps.cost_per_hour * max(0.0, requirements.duration_s) / 3600.0
            if cost > requirements.max_cost:
                return False
        return True

    def _validate(self, requirements: ComputeRequirements) -> None:
        if requirements.privacy == "high" and self._policy.require_local_for_high_privacy:
            local_ok = any(
                caps.local and self._available(name, requirements, caps)
                and self._capable(requirements, caps)
                and self._permitted(name, caps, requirements)
                for name, caps in self._providers.items())
            if not local_ok:
                raise PolicyRejectionError(
                    "high-privacy work requires local execution, "
                    "which is unavailable or incapable")
        if requirements.max_cost >= 0:
            affordable = any(
                caps.cost_per_hour * max(0.0, requirements.duration_s) / 3600.0
                <= requirements.max_cost
                for caps in self._providers.values())
            if not affordable:
                raise PolicyRejectionError("no provider within max_cost")

    def _why_empty(self, requirements: ComputeRequirements) -> str:
        return (f"requirements {requirements.model_dump()} match no provider "
                f"under policy {self._policy.model_dump()}")

    @staticmethod
    def _reason(requirements: ComputeRequirements,
                caps: ProviderCapabilities) -> str:
        bits = []
        if caps.local:
            bits.append("local")
        if requirements.gpu and caps.gpu:
            bits.append(f"gpu+{caps.vram_gb}GB VRAM")
        bits.append(f"${caps.cost_per_hour:.2f}/h")
        return ", ".join(bits) or "suitable"
