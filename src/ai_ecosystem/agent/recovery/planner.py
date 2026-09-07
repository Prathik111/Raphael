"""Recovery planning: replacement plans through the same gates (Gate 10).

A replan provider proposes; this module disposes. Every candidate passes
structure validation, per-tool risk assessment, and policy evaluation
before it may execute. Identical re-proposals are rejected as replan
loops. There is no autonomous reasoning loop here.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Optional

from ai_ecosystem.agent.planner.validator import PlanValidator
from ai_ecosystem.core.errors.exceptions import (
    AuthorizationDeniedError,
    PlanValidationError,
)
from ai_ecosystem.core.models.domain import Plan, ToolCall
from ai_ecosystem.security.policy.engines import (
    PolicyEngine,
    RiskContext,
    RiskEngine,
)
from ai_ecosystem.tools.registry.registry import ToolRegistry

ReplanProvider = Callable[[str, Plan, str], Plan]


def plan_version(plan: Plan) -> str:
    """Stable identity for a plan (loop detection)."""
    return hashlib.sha256(plan.model_dump_json().encode("utf-8")).hexdigest()


class RecoveryPlanner:
    """Validates replacement plans against structure, risk, and policy."""

    def __init__(
        self,
        registry: ToolRegistry,
        risk_engine: Optional[RiskEngine] = None,
        policy_engine: Optional[PolicyEngine] = None,
        replan_provider: Optional[ReplanProvider] = None,
        agent_id: str = "",
    ) -> None:
        self._registry = registry
        self._risk = risk_engine or RiskEngine()
        self._policy = policy_engine or PolicyEngine()
        self._replan_provider = replan_provider
        self._agent_id = agent_id

    @property
    def known_tools(self) -> set[str]:
        """Tool names the validator accepts."""
        return {tool.name for tool in self._registry.list_tools()}

    def request_replan(
        self, task_id: str, failed_plan: Plan, reason: str, seen_versions: set[str]
    ) -> Plan:
        """Obtain and fully vet a replacement plan (raises when unusable)."""
        if self._replan_provider is None:
            raise PlanValidationError("no replan provider configured")
        candidate = self._replan_provider(task_id, failed_plan, reason)
        version = plan_version(candidate)
        if version in seen_versions:
            raise PlanValidationError(
                "replacement plan identical to a previous version (replan loop)"
            )
        PlanValidator(self.known_tools).validate(candidate)
        context = RiskContext(agent_id=self._agent_id)
        for step in candidate.steps:
            for tool_name in step.tools:
                tool = self._registry.get(tool_name)
                if tool is None:  # pragma: no cover -- validator catches first
                    raise PlanValidationError(f"unknown tool {tool_name!r}")
                assessment = self._risk.assess(
                    task_id, tool, ToolCall(task_id=task_id, tool=tool_name), context
                )
                granted, policy_reason = self._policy.evaluate(
                    assessment, tool_name, self._agent_id
                )
                if not granted:
                    raise AuthorizationDeniedError(task_id, tool_name, policy_reason)
        return candidate
