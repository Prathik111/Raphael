"""Failure classification (Gate 10).

Maps observed failures to a FailureClass. Classification is descriptive;
the RecoveryPolicy decides what to do. Deliberately conservative: only
well-understood transient classes are ever retryable, and permission
failures are never retryable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ai_ecosystem.core.errors.exceptions import (
    AuthorizationDeniedError,
    ModelError,
    PlanValidationError,
    ToolTimeoutError,
)
from ai_ecosystem.core.models.domain import ToolResult, VerificationResult
from ai_ecosystem.core.models.enums import VerificationStatus


class FailureClass(str, Enum):
    """Root-cause categories for a failed execute/verify cycle."""

    TRANSIENT = "TRANSIENT"
    TIMEOUT = "TIMEOUT"
    TOOL_FAILURE = "TOOL_FAILURE"
    PERMISSION_FAILURE = "PERMISSION_FAILURE"
    MODEL_FAILURE = "MODEL_FAILURE"
    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"
    VERIFICATION_FAILURE = "VERIFICATION_FAILURE"
    LOGICAL_FAILURE = "LOGICAL_FAILURE"
    UNKNOWN = "UNKNOWN"


@dataclass
class Classification:
    """A classified failure with human-readable detail."""

    failure_class: FailureClass
    detail: str = ""
    source: str = ""


_TRANSIENT_MARKERS = (
    "transient",
    "temporary",
    "try again",
    "connection reset",
    "connection refused",
    "unavailable",
)


class FailureClassifier:
    """Pure functions from evidence to FailureClass."""

    @staticmethod
    def classify_tool_result(result: ToolResult) -> Classification:
        """Classify one failed tool result by its error text."""
        error = (result.error or "").lower()
        if "denied" in error:
            return Classification(
                FailureClass.PERMISSION_FAILURE, result.error or "", "tool_result"
            )
        if "timed out" in error:
            return Classification(FailureClass.TIMEOUT, result.error or "", "tool_result")
        if any(marker in error for marker in _TRANSIENT_MARKERS):
            return Classification(FailureClass.TRANSIENT, result.error or "", "tool_result")
        return Classification(
            FailureClass.TOOL_FAILURE, result.error or "tool failed", "tool_result"
        )

    @staticmethod
    def classify_verification(result: VerificationResult) -> Classification:
        """Classify a non-passing verification outcome."""
        if result.status is VerificationStatus.FAILED:
            return Classification(FailureClass.VERIFICATION_FAILURE, result.reason, "verification")
        return Classification(
            FailureClass.UNKNOWN,
            f"verification ended {result.status.value}: {result.reason}",
            "verification",
        )

    @staticmethod
    def classify_exception(exc: BaseException) -> Classification:
        """Classify a raised error (planner backends, providers, policy)."""
        if isinstance(exc, AuthorizationDeniedError):
            return Classification(FailureClass.PERMISSION_FAILURE, str(exc), "exception")
        if isinstance(exc, ToolTimeoutError):
            return Classification(FailureClass.TIMEOUT, str(exc), "exception")
        if isinstance(exc, ModelError):
            return Classification(FailureClass.MODEL_FAILURE, str(exc), "exception")
        if isinstance(exc, PlanValidationError):
            return Classification(FailureClass.LOGICAL_FAILURE, str(exc), "exception")
        return Classification(FailureClass.UNKNOWN, str(exc), "exception")
