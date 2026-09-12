"""Data classification and egress decisions for every trust boundary."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from ai_ecosystem.core.secrets import looks_like_secret_value, looks_secret


class DataClass(str, Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    PERSONAL = "PERSONAL"
    SENSITIVE = "SENSITIVE"
    SECRET = "SECRET"


class EgressDecision(BaseModel):
    allowed: bool
    classification: DataClass
    destination: str
    reason: str


class DataPolicy(BaseModel):
    """Default-deny egress policy. Explicit destinations may be allowlisted."""

    local_destinations: set[str] = Field(default_factory=lambda: {"local", "loopback"})
    approved_sensitive_destinations: set[str] = Field(default_factory=set)
    approved_personal_destinations: set[str] = Field(default_factory=set)

    def classify(self, value: object, *, declared: DataClass | None = None) -> DataClass:
        if declared is DataClass.SECRET:
            return DataClass.SECRET
        if _contains_secret(value):
            return DataClass.SECRET
        if declared is not None:
            return declared
        if _looks_personal(value):
            return DataClass.PERSONAL
        return DataClass.INTERNAL

    def decide(
        self, value: object, destination: str, *, declared: DataClass | None = None
    ) -> EgressDecision:
        classification = self.classify(value, declared=declared)
        if destination in self.local_destinations:
            return EgressDecision(True, classification, destination, "local destination")
        if classification is DataClass.SECRET:
            return EgressDecision(
                False,
                classification,
                destination,
                "SECRET data may never leave the local trust boundary",
            )
        if classification is DataClass.SENSITIVE:
            allowed = destination in self.approved_sensitive_destinations
            return EgressDecision(
                allowed,
                classification,
                destination,
                "approved sensitive destination"
                if allowed
                else "SENSITIVE destination not approved",
            )
        if classification is DataClass.PERSONAL:
            allowed = destination in self.approved_personal_destinations
            return EgressDecision(
                allowed,
                classification,
                destination,
                "approved personal destination" if allowed else "PERSONAL destination not approved",
            )
        return EgressDecision(
            destination in self.approved_sensitive_destinations
            or destination in self.approved_personal_destinations,
            classification,
            destination,
            "destination is not explicitly approved",
        )


def _contains_secret(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            looks_secret(str(k)) or _contains_secret(v) or looks_like_secret_value(v)
            for k, v in value.items()
        )
    if isinstance(value, list | tuple | set | frozenset):
        return any(_contains_secret(v) for v in value)
    return looks_like_secret_value(value)


def _looks_personal(value: object) -> bool:
    if isinstance(value, dict):
        keys = {str(k).lower() for k in value}
        return bool(
            keys & {"email", "phone", "address", "name", "location", "dob", "date_of_birth"}
        )
    return False


def require_egress(
    policy: DataPolicy, value: object, destination: str, *, declared: DataClass | None = None
) -> None:
    """Raise when data may not cross the requested destination boundary."""
    decision = policy.decide(value, destination, declared=declared)
    if not decision.allowed:
        raise PermissionError(decision.reason)
