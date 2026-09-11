"""Learning safety: policy, risk, drift, kill switch (Gate 33).

CRITICAL changes (anything touching permissions, policy, credentials,
sandboxing, or authorization) can never be adopted -- not quietly, not
loudly, not ever. The governor sits in front of every adoption call,
and the kill switch stops all future adoptions immediately.
"""

from __future__ import annotations

from collections import Counter
from enum import Enum
from typing import Any

from ai_ecosystem.core.errors.exceptions import DomainValidationError
from ai_ecosystem.core.models.base import Entity
from ai_ecosystem.learning.models import LearningProposal, ProposalKind

_CRITICAL_HINTS = (
    "permission",
    "permissions",
    "authorize",
    "authorized",
    "authorization",
    "policy",
    "policies",
    "credential",
    "credentials",
    "secret",
    "secrets",
    "sandbox",
    "password",
    "passwords",
    "token",
    "tokens",
    "sudo",
    "admin",
)
_HIGH_HINTS = ("skill", "allow", "always", "every", "bypass", "disable")
_MEDIUM_HINTS = ("workflow", "prefer", "default")


def _has_hint(content: str, hints: tuple[str, ...]) -> bool:
    """Whole-token match: "keyboard" is not "key", "allowance" not "allow"."""
    import re

    tokens = set(re.split(r"[^a-z0-9]+", content.lower()))
    return any(hint in tokens for hint in hints)


class LearningMode(str, Enum):
    """Operator learning posture."""

    DISABLED = "DISABLED"
    SUGGEST_ONLY = "SUGGEST_ONLY"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    AUTO_APPROVE_LOW_RISK = "AUTO_APPROVE_LOW_RISK"


class LearningRisk(str, Enum):
    """Risk tier of one learning change."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class LearningPolicyState(Entity):
    """Persisted governor posture (single-row convention, id 'global')."""

    mode: LearningMode = LearningMode.APPROVAL_REQUIRED
    killed: bool = False


def classify_change(kind: ProposalKind, content: str) -> LearningRisk:
    """Risk tier for a proposed change (content-scanned, conservative)."""
    if _has_hint(content, _CRITICAL_HINTS):
        return LearningRisk.CRITICAL
    if _has_hint(content, _HIGH_HINTS):
        return LearningRisk.HIGH
    if kind is ProposalKind.MODEL_HINT:
        return LearningRisk.LOW
    if kind is ProposalKind.COMMUNICATION:
        return LearningRisk.LOW
    if _has_hint(content, _MEDIUM_HINTS):
        return LearningRisk.MEDIUM
    return LearningRisk.MEDIUM


class DriftDetector:
    """Session-scoped anomaly watch over adoptions and proposals."""

    def __init__(self, risky_rate_threshold: float = 0.5, contradiction_window: int = 50) -> None:
        self._adopted: list[LearningProposal] = []
        self._proposed_risky = 0
        self._proposed_total = 0
        self._risky_rate_threshold = risky_rate_threshold
        self._window = contradiction_window

    def note_proposal(self, proposal: LearningProposal, risk: LearningRisk) -> None:
        """Feed one proposal into the drift counters."""
        self._proposed_total += 1
        if risk in (LearningRisk.HIGH, LearningRisk.CRITICAL):
            self._proposed_risky += 1

    def note_adoption(self, proposal: LearningProposal) -> None:
        """Feed one adoption (bounded window)."""
        self._adopted.append(proposal)
        del self._adopted[: -self._window]

    def suspicious(self) -> list[str]:
        """Human-readable drift findings (empty when healthy)."""
        findings = []
        if self._proposed_total >= 4 and (
            self._proposed_risky / self._proposed_total >= self._risky_rate_threshold
        ):
            findings.append(f"risky proposal rate {self._proposed_risky}/{self._proposed_total}")
        kinds = Counter(p.kind.value for p in self._adopted)
        model_switches = kinds.get(ProposalKind.MODEL_HINT.value, 0)
        if model_switches >= 3:
            findings.append(f"unexpected model switching x{model_switches}")
        scopes: dict[str, list[LearningProposal]] = {}
        for proposal in self._adopted:
            scopes.setdefault(proposal.scope, []).append(proposal)
        for scope, items in scopes.items():
            if len({p.content for p in items}) > 3 and len(items) >= 4:
                findings.append(f"contradictory preferences in scope {scope!r}")
        return findings


class LearningGovernor:
    """Gatekeeper for every adoption (kill switch included)."""

    def __init__(
        self, mode: LearningMode = LearningMode.APPROVAL_REQUIRED, killed: bool = False
    ) -> None:
        self._mode = mode
        self._killed = killed

    @property
    def mode(self) -> LearningMode:
        """Current posture."""
        return self._mode

    def set_mode(self, mode: LearningMode) -> None:
        """Operator posture change."""
        self._mode = mode

    def kill(self) -> None:
        """Global disable: no further adoptions, immediately."""
        self._killed = True

    def revive(self) -> None:
        """Re-enable after a kill (explicit operator action)."""
        self._killed = False

    @property
    def killed(self) -> bool:
        """Whether the kill switch is engaged."""
        return self._killed

    def classify(self, proposal: LearningProposal) -> LearningRisk:
        """Risk tier of a proposal."""
        return classify_change(proposal.kind, proposal.content)

    def may_adopt(self, proposal: LearningProposal, approved: bool = False) -> bool:
        """Adoption verdict under the current posture."""
        if self._killed or self._mode is LearningMode.DISABLED:
            return False
        risk = self.classify(proposal)
        if risk is LearningRisk.CRITICAL:
            return False
        if self._mode is LearningMode.SUGGEST_ONLY:
            return False
        if self._mode is LearningMode.APPROVAL_REQUIRED:
            return approved
        if self._mode is LearningMode.AUTO_APPROVE_LOW_RISK:
            return approved or risk is LearningRisk.LOW
        return False

    def persist(self, repository: Any) -> Any:
        """Save posture (single row id 'global')."""
        state = LearningPolicyState(id="global", mode=self._mode, killed=self._killed)
        existing = repository.get("global")
        if existing is None:
            return repository.create(state)
        existing.mode = self._mode
        existing.killed = self._killed
        existing.touch()
        return repository.update(existing)

    @staticmethod
    def load(repository: Any) -> LearningGovernor:
        """Restore posture (defaults when never saved)."""
        state = repository.get("global")
        if state is None:
            return LearningGovernor()
        return LearningGovernor(mode=state.mode, killed=state.killed)


def check_not_security(content: str) -> None:
    """Raise on anything resembling a security change (defense helper)."""
    if _has_hint(content, _CRITICAL_HINTS):
        raise DomainValidationError("security content cannot be learned")
