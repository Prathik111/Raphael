"""Verifier: ExecutionResult/ToolResults in, structured verdict out (Gate 9).

Separation is structural: the Verifier accepts *observed* results only.
It holds no reference to ParallelExecutor, TaskManager, or any model --
importing this module never pulls in execution or reasoning.
"""

from __future__ import annotations

from typing import Any

from ai_ecosystem.agent.verifier.strategies import (
    ArtifactExistsStrategy,
    ArtifactPropertyStrategy,
    CommandResultStrategy,
    TestCommandStrategy,
    VerificationStrategy,
    VerificationTarget,
)
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.domain import ToolResult, VerificationResult
from ai_ecosystem.core.models.enums import EventType, VerificationStatus
from ai_ecosystem.core.persistence.sqlite import SqliteVerificationRepository

_TERMINAL_EVENT = {
    VerificationStatus.PASSED: EventType.VERIFICATION_PASSED,
    VerificationStatus.FAILED: EventType.VERIFICATION_FAILED,
    VerificationStatus.INCONCLUSIVE: EventType.VERIFICATION_INCONCLUSIVE,
    VerificationStatus.ERROR: EventType.VERIFICATION_ERROR,
}


class Verifier:
    """Aggregates deterministic strategy findings into one verdict."""

    def __init__(
        self,
        strategies: dict[str, VerificationStrategy] | None = None,
        bus: EventBus | None = None,
        repository: SqliteVerificationRepository | None = None,
    ) -> None:
        defaults: dict[str, VerificationStrategy] = {
            CommandResultStrategy.name: CommandResultStrategy(),
            ArtifactExistsStrategy.name: ArtifactExistsStrategy(),
            ArtifactPropertyStrategy.name: ArtifactPropertyStrategy(),
        }
        if strategies:
            defaults.update(strategies)
        self._strategies = defaults
        self._bus = bus
        self._repository = repository

    @property
    def strategy_names(self) -> list[str]:
        """Registered strategy names (sorted)."""
        return sorted(self._strategies)

    def with_test_command(self, runner: Any, registry: Any) -> Verifier:
        """Return a sibling verifier plus the test-command strategy."""
        strategies = dict(self._strategies)
        strategies[TestCommandStrategy.name] = TestCommandStrategy(runner, registry)
        return Verifier(strategies, self._bus, self._repository)

    def verify(
        self,
        task_id: str,
        step_id: str,
        tool_results: list[ToolResult],
        criteria: list[dict[str, Any]],
        root: str = "",
    ) -> VerificationResult:
        """Evaluate criteria against evidence; always returns a result."""
        self._emit(
            EventType.VERIFICATION_STARTED,
            task_id,
            {"step_id": step_id, "criteria": len(criteria)},
        )
        if not criteria:
            return self._finish(
                task_id,
                step_id,
                VerificationStatus.INCONCLUSIVE,
                ["no completion criteria to evaluate"],
                ["nothing to check"],
                "no criteria supplied",
            )
        target = VerificationTarget(
            tool_results=list(tool_results), root=root, task_id=task_id
        )
        checks: list[str] = []
        evidence: list[str] = []
        failures: list[str] = []
        inconclusive = False
        for index, criterion in enumerate(criteria):
            name = criterion.get("strategy", "")
            strategy = self._strategies.get(name)
            if strategy is None:
                return self._finish(
                    task_id,
                    step_id,
                    VerificationStatus.ERROR,
                    checks,
                    evidence,
                    f"criterion {index}: unknown strategy {name!r}",
                )
            try:
                finding = strategy.check(target, criterion.get("params", {}))
            except Exception as exc:  # noqa: BLE001 -- mapped to ERROR, never raised
                return self._finish(
                    task_id,
                    step_id,
                    VerificationStatus.ERROR,
                    checks,
                    evidence,
                    f"criterion {index} ({name}) raised: {exc}",
                )
            checks.append(f"{name}: {finding.reason}")
            evidence.extend(finding.evidence)
            if finding.passed is False:
                failures.append(f"{name}: {finding.reason}")
            elif finding.passed is None:
                inconclusive = True
        if failures:
            status = VerificationStatus.FAILED
            reason = "; ".join(failures)
        elif inconclusive:
            status = VerificationStatus.INCONCLUSIVE
            reason = "some checks could not be determined"
        else:
            status = VerificationStatus.PASSED
            reason = "all completion criteria satisfied"
        return self._finish(task_id, step_id, status, checks, evidence, reason)

    def _finish(
        self,
        task_id: str,
        step_id: str,
        status: VerificationStatus,
        checks: list[str],
        evidence: list[str],
        reason: str,
    ) -> VerificationResult:
        result = VerificationResult(
            task_id=task_id,
            step_id=step_id,
            status=status,
            strategy=",".join(self.strategy_names),
            checks=checks,
            evidence=evidence,
            reason=reason,
        )
        if self._repository is not None:
            self._repository.create(result)
        self._emit(
            _TERMINAL_EVENT[status],
            task_id,
            {"step_id": step_id, "status": status.value, "reason": reason},
        )
        return result

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(event_type=event_type, task_id=task_id, payload=payload)
            )
