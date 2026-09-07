"""Recovery policy: bounded retries and explicit action mapping (Gate 10).

Bounds are structural, not advisory: the engine's execution loop cannot
exceed them, so infinite retry/replan loops are impossible by
construction. Permission failures are never retried under any policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ai_ecosystem.agent.recovery.classification import Classification, FailureClass


class RecoveryAction(str, Enum):
    """Explicit recovery actions; every decision names one."""

    RETRY = "RETRY"
    REPLAN = "REPLAN"
    SKIP = "SKIP"
    ESCALATE = "ESCALATE"
    FAIL = "FAIL"


@dataclass
class RetryPolicy:
    """Bounded retry schedule with backoff accounting (no sleeping here)."""

    max_attempts: int = 3
    base_delay_s: float = 0.0
    max_delay_s: float = 30.0

    def delay_for(self, attempt: int) -> float:
        """Backoff for retry number ``attempt`` (1-based)."""
        if attempt < 1:
            return 0.0
        return min(self.base_delay_s * (2 ** (attempt - 1)), self.max_delay_s)

    def should_retry(self, classification: Classification, retries_used: int) -> bool:
        """True only for retryable classes inside the attempt budget."""
        if classification.failure_class is FailureClass.PERMISSION_FAILURE:
            return False
        if classification.failure_class in (
            FailureClass.TIMEOUT,
            FailureClass.TRANSIENT,
            FailureClass.TOOL_FAILURE,
            FailureClass.MODEL_FAILURE,
        ):
            return retries_used < self.max_attempts
        return False


@dataclass
class RecoveryPolicy:
    """Maps failure classes to actions; overrides are explicit and total."""

    retries: RetryPolicy = field(default_factory=RetryPolicy)
    max_replans: int = 1
    overrides: dict[FailureClass, RecoveryAction] = field(default_factory=dict)

    def action_for(
        self, classification: Classification, retries_used: int, replans_used: int
    ) -> RecoveryAction:
        """Decide the single next action for a classified failure."""
        if classification.failure_class in self.overrides:
            return self.overrides[classification.failure_class]
        kind = classification.failure_class
        if kind is FailureClass.PERMISSION_FAILURE:
            return RecoveryAction.ESCALATE
        if kind in (
            FailureClass.TIMEOUT,
            FailureClass.TRANSIENT,
            FailureClass.TOOL_FAILURE,
            FailureClass.MODEL_FAILURE,
        ):
            if self.retries.should_retry(classification, retries_used):
                return RecoveryAction.RETRY
            return RecoveryAction.ESCALATE
        if kind is FailureClass.VERIFICATION_FAILURE:
            if replans_used < self.max_replans:
                return RecoveryAction.REPLAN
            return RecoveryAction.ESCALATE
        return RecoveryAction.FAIL
