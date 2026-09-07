"""Recovery engine: the bounded execute/verify/recover loop (Gate 10).

Lifecycle::

    EXECUTE -> VERIFY -> PASS -> CONTINUE (RECOVERED)
    EXECUTE -> VERIFY -> FAIL -> CLASSIFY -> RETRY/REPLAN/SKIP/ESCALATE/FAIL
        -> EXECUTE -> VERIFY AGAIN ...

A recovered task is NOT successful until verification passes. Bounds
(``max_attempts``, ``max_replans``) are enforced by loop construction,
not by convention, and every decision is audited as a RecoveryRecord.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from ai_ecosystem.agent.executor.executor import ExecutionResult, OverallStatus
from ai_ecosystem.agent.recovery.classification import (
    Classification,
    FailureClass,
    FailureClassifier,
)
from ai_ecosystem.agent.recovery.escalation import EscalationManager
from ai_ecosystem.agent.recovery.planner import RecoveryPlanner, plan_version
from ai_ecosystem.agent.recovery.policy import RecoveryAction, RecoveryPolicy
from ai_ecosystem.core.errors.exceptions import (
    AuthorizationDeniedError,
    PlanValidationError,
)
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.domain import Plan, VerificationResult
from ai_ecosystem.core.models.enums import EventType, VerificationStatus

ExecuteFn = Callable[[Plan, dict], ExecutionResult]
VerifyFn = Callable[[str, Plan, ExecutionResult], VerificationResult]


class OutcomeStatus(str, Enum):
    """Terminal states of a recovery run."""

    RECOVERED = "RECOVERED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"
    PARTIAL = "PARTIAL"


@dataclass
class RecoveryRecord:
    """One audited step of the recovery loop."""

    attempt: int = 0
    classification: FailureClass = FailureClass.UNKNOWN
    action: RecoveryAction = RecoveryAction.FAIL
    reason: str = ""
    verification: str = ""


@dataclass
class RecoveryOutcome:
    """Terminal outcome plus the full audit trail."""

    task_id: str = ""
    status: OutcomeStatus = OutcomeStatus.FAILED
    attempts: int = 0
    retries: int = 0
    replans: int = 0
    plan_versions: list[str] = field(default_factory=list)
    audit: list[RecoveryRecord] = field(default_factory=list)
    final_verification: Optional[VerificationResult] = None
    reason: str = ""


class RecoveryEngine:
    """Drives injected execute/verify callables through a bounded loop."""

    def __init__(
        self,
        policy: Optional[RecoveryPolicy] = None,
        planner: Optional[RecoveryPlanner] = None,
        escalation: Optional[EscalationManager] = None,
        bus: Optional[EventBus] = None,
    ) -> None:
        self._policy = policy or RecoveryPolicy()
        self._planner = planner
        self._escalation = escalation or EscalationManager(bus)
        self._bus = bus

    def run(
        self,
        task_id: str,
        plan: Plan,
        execute_fn: ExecuteFn,
        verify_fn: VerifyFn,
        arguments: Optional[dict[str, Any]] = None,
        max_attempts: int = 3,
    ) -> RecoveryOutcome:
        """Execute, verify, and recover until terminal (bounded)."""
        return self.run_with_prior(
            task_id, plan, execute_fn, verify_fn, arguments, max_attempts, None, None)

    def run_with_prior(
        self,
        task_id: str,
        plan: Plan,
        execute_fn: ExecuteFn,
        verify_fn: VerifyFn,
        arguments: Optional[dict[str, Any]] = None,
        max_attempts: int = 3,
        prior_result: Optional[ExecutionResult] = None,
        prior_verification: Optional[VerificationResult] = None,
    ) -> RecoveryOutcome:
        """Like run(), but starts from an already-observed failure.

        The orchestrator hands over its first execute/verify cycle so the
        audit trail is complete: the prior failure is classified and
        decided, not silently re-executed. Prior executions count toward
        the attempt budget.
        """
        args = arguments or {}
        versions = [plan_version(plan)]
        seen = set(versions)
        audit: list[RecoveryRecord] = []
        retries = 0
        replans = 0
        current = plan
        executions = 0
        result = prior_result
        verification = prior_verification
        if result is not None:
            executions = 1
            if (verification is not None
                    and result.status is OverallStatus.COMPLETED
                    and verification.status is VerificationStatus.PASSED):
                return RecoveryOutcome(
                    task_id=task_id, status=OutcomeStatus.RECOVERED,
                    attempts=0, retries=0, replans=0,
                    plan_versions=versions, audit=audit,
                    final_verification=verification,
                    reason="verification passed",
                )

        while True:
            if result is None:
                if executions >= max_attempts:
                    break
                result = execute_fn(current, args)
                executions += 1
                verification = None
                if result.status is OverallStatus.CANCELLED:
                    return self._terminal(
                        task_id, OutcomeStatus.FAILED, executions, retries, replans,
                        versions, audit, verification, "execution cancelled",
                    )
            if verification is None:
                try:
                    verification = verify_fn(task_id, current, result)
                except Exception as exc:  # noqa: BLE001 -- verification must not escape
                    classification = FailureClassifier.classify_exception(exc)
                    return self._give_up(
                        task_id, executions, retries, replans, versions, audit,
                        classification, f"verification raised: {exc}",
                    )
            if verification.status is VerificationStatus.PASSED:
                return RecoveryOutcome(
                    task_id=task_id, status=OutcomeStatus.RECOVERED,
                    attempts=executions, retries=retries, replans=replans,
                    plan_versions=versions, audit=audit,
                    final_verification=verification,
                    reason="verification passed",
                )
            classification = self._classify(result, verification)
            action = self._policy.action_for(classification, retries, replans)
            record = RecoveryRecord(
                attempt=executions,
                classification=classification.failure_class,
                action=action,
                reason=classification.detail,
                verification=verification.status.value,
            )
            audit.append(record)
            self._emit_decided(task_id, record)

            if action is RecoveryAction.RETRY:
                retries += 1
                result = None
                verification = None
                continue
            if action is RecoveryAction.REPLAN:
                if self._planner is None:
                    return self._terminal(
                        task_id, OutcomeStatus.FAILED, executions, retries, replans,
                        versions, audit, verification, "replan requested but no planner",
                    )
                try:
                    candidate = self._planner.request_replan(
                        task_id, current, classification.detail, seen
                    )
                except (PlanValidationError, AuthorizationDeniedError) as exc:
                    audit.append(RecoveryRecord(
                        attempt=executions, classification=classification.failure_class,
                        action=RecoveryAction.FAIL,
                        reason=f"replacement plan rejected: {exc}",
                        verification=verification.status.value,
                    ))
                    return self._terminal(
                        task_id, OutcomeStatus.FAILED, executions, retries, replans,
                        versions, audit, verification,
                        f"replacement plan rejected: {exc}",
                    )
                replans += 1
                seen.add(plan_version(candidate))
                versions.append(plan_version(candidate))
                current = candidate
                result = None
                verification = None
                continue
            if action is RecoveryAction.SKIP:
                return self._terminal(
                    task_id, OutcomeStatus.PARTIAL, executions, retries, replans,
                    versions, audit, verification,
                    f"skipped after {classification.failure_class.value}",
                )
            if action is RecoveryAction.ESCALATE:
                self._escalation.escalate(
                    task_id, classification.detail,
                    [verification.reason, verification.status.value],
                )
                return self._terminal(
                    task_id, OutcomeStatus.ESCALATED, executions, retries, replans,
                    versions, audit, verification,
                    f"escalated: {classification.detail}",
                )
            return self._terminal(
                task_id, OutcomeStatus.FAILED, executions, retries, replans,
                versions, audit, verification,
                f"failed: {classification.detail}",
            )
        return self._terminal(
            task_id, OutcomeStatus.FAILED, executions, retries, replans,
            versions, audit, verification, "attempt budget exhausted",
        )

    def _classify(
        self, result: ExecutionResult, verification: VerificationResult
    ) -> Classification:
        """Root-cause first: execution failures dominate verification ones."""
        denied = next(
            (r for r in self._all_results(result) if "denied" in (r.error or "").lower()),
            None,
        )
        if denied is not None:
            return FailureClassifier.classify_tool_result(denied)
        if result.timed_out:
            return Classification(FailureClass.TIMEOUT, "step deadline exceeded", "execution")
        failed = next((r for r in self._all_results(result) if not r.success), None)
        if failed is not None:
            return FailureClassifier.classify_tool_result(failed)
        if result.status is OverallStatus.FAILED:
            if result.skipped:
                return Classification(
                    FailureClass.DEPENDENCY_FAILURE,
                    f"steps skipped: {', '.join(result.skipped)}", "execution",
                )
            return Classification(FailureClass.UNKNOWN, "execution failed", "execution")
        return FailureClassifier.classify_verification(verification)

    @staticmethod
    def _all_results(result: ExecutionResult) -> list:
        """Every observed tool result across the execution (plan-step order)."""
        return result.all_tool_results()

    def _give_up(
        self, task_id: str, attempt: int, retries: int, replans: int,
        versions: list[str], audit: list[RecoveryRecord],
        classification: Classification, reason: str,
    ) -> RecoveryOutcome:
        audit.append(RecoveryRecord(
            attempt=attempt, classification=classification.failure_class,
            action=RecoveryAction.FAIL, reason=reason, verification="ERROR",
        ))
        self._emit_decided(task_id, audit[-1])
        return self._terminal(
            task_id, OutcomeStatus.FAILED, attempt, retries, replans,
            versions, audit, None, reason,
        )

    def _terminal(
        self, task_id: str, status: OutcomeStatus, attempt: int,
        retries: int, replans: int, versions: list[str],
        audit: list[RecoveryRecord], verification: Optional[VerificationResult],
        reason: str,
    ) -> RecoveryOutcome:
        return RecoveryOutcome(
            task_id=task_id, status=status, attempts=attempt,
            retries=retries, replans=replans, plan_versions=versions,
            audit=audit, final_verification=verification, reason=reason,
        )

    def _emit_decided(self, task_id: str, record: RecoveryRecord) -> None:
        if self._bus is not None:
            self._bus.publish(Event(
                event_type=EventType.RECOVERY_DECIDED, task_id=task_id,
                payload={
                    "attempt": record.attempt,
                    "classification": record.classification.value,
                    "action": record.action.value,
                    "reason": record.reason,
                },
            ))
