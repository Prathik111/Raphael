"""Security envelope: trust levels and capability danger (Gate 29).

Trust is assigned, never claimed: only a SYSTEM actor can raise a
component's level, and levels only ever gate *additional* checks --
they never grant permissions by themselves. Authorization stays the
single choke point; this module names who the actors are and how much
skepticism each deserves.
"""

from __future__ import annotations

from enum import Enum

from ai_ecosystem.core.errors.exceptions import DomainValidationError


class TrustLevel(str, Enum):
    """Skepticism tiers, lowest first."""

    UNTRUSTED = "UNTRUSTED"
    LIMITED = "LIMITED"
    TRUSTED = "TRUSTED"
    SYSTEM = "SYSTEM"


_ORDER = [TrustLevel.UNTRUSTED, TrustLevel.LIMITED, TrustLevel.TRUSTED,
          TrustLevel.SYSTEM]


def meets(actual: TrustLevel, required: TrustLevel) -> bool:
    """True when actual is at least the required tier."""
    return _ORDER.index(actual) >= _ORDER.index(required)


CAPABILITY_RISK = {
    "filesystem.read": "LOW",
    "filesystem.write": "MEDIUM",
    "terminal.execute": "HIGH",
    "network.request": "HIGH",
    "cloud.submit": "HIGH",
    "memory.write": "MEDIUM",
    "policy.modify": "CRITICAL",
}

DANGEROUS_CAPABILITIES = frozenset(
    {name for name, risk in CAPABILITY_RISK.items() if risk in ("HIGH", "CRITICAL")})


class ComponentTrust:
    """Registry of component trust (assignment-only, no self-elevation)."""

    def __init__(self) -> None:
        self._levels: dict[str, TrustLevel] = {}

    def level_of(self, component: str) -> TrustLevel:
        """Current tier (UNTRUSTED when never assigned)."""
        return self._levels.get(component, TrustLevel.UNTRUSTED)

    def assign(self, component: str, level: TrustLevel,
               actor: str, actor_trust: TrustLevel) -> TrustLevel:
        """Set a tier; only SYSTEM actors may assign (up or down)."""
        if actor_trust is not TrustLevel.SYSTEM:
            raise DomainValidationError(
                f"actor {actor!r} cannot assign trust (SYSTEM required)")
        self._levels[component] = level
        return level

    def require(self, component: str, minimum: TrustLevel) -> None:
        """Raise unless the component meets the minimum tier."""
        if not meets(self.level_of(component), minimum):
            raise DomainValidationError(
                f"component {component!r} is {self.level_of(component).value}, "
                f"requires {minimum.value}")


def capability_risk(capability: str) -> str:
    """Advisory danger of a capability name (UNKNOWN when unlisted)."""
    return CAPABILITY_RISK.get(capability, "UNKNOWN")


def is_dangerous(capability: str) -> bool:
    """True for HIGH/CRITICAL capabilities."""
    return capability in DANGEROUS_CAPABILITIES
