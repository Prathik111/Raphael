"""Public recovery API."""

from ai_ecosystem.agent.recovery.classification import (
    Classification,
    FailureClass,
    FailureClassifier,
)
from ai_ecosystem.agent.recovery.engine import (
    OutcomeStatus,
    RecoveryEngine,
    RecoveryOutcome,
    RecoveryRecord,
)
from ai_ecosystem.agent.recovery.escalation import Escalation, EscalationManager
from ai_ecosystem.agent.recovery.planner import RecoveryPlanner, plan_version
from ai_ecosystem.agent.recovery.policy import (
    RecoveryAction,
    RecoveryPolicy,
    RetryPolicy,
)

__all__ = [
    "Classification",
    "Escalation",
    "EscalationManager",
    "FailureClass",
    "FailureClassifier",
    "OutcomeStatus",
    "RecoveryAction",
    "RecoveryEngine",
    "RecoveryOutcome",
    "RecoveryPlanner",
    "RecoveryPolicy",
    "RecoveryRecord",
    "RetryPolicy",
    "plan_version",
]
