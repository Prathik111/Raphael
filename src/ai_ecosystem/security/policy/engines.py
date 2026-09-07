"""Permission + risk engines (BUILD_PLAN Gate 6, Part 5).

Hard security boundary. Every tool call passes::

    schema validation -> risk -> policy -> authorization

Risk considers tool, arguments, target, scope, policy, task, agent, and
environment -- never the tool name alone. The single choke point is
:class:`AuthorizationManager`, which satisfies the runner's
``Authorizer`` protocol, so there is no path from model output to a
tool handler that skips this module.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ai_ecosystem.core.errors.exceptions import (
    AuthorizationDeniedError,
    DomainValidationError,
)
from ai_ecosystem.core.models.domain import (
    Permission,
    RiskAssessment,
    Tool,
    ToolCall,
)
from ai_ecosystem.core.models.enums import PermissionDecision, RiskLevel
from ai_ecosystem.tools.registry.registry import ToolRegistry

_LEVEL_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

_SHELL_TOKENS = (";", "&&", "||", "$(", "`", "|")


class RiskContext(BaseModel):
    """Everything about *who/where* a call runs, besides the call itself."""

    agent_id: str = ""
    environment: str = "local"
    root: str = ""


class RiskEngine:
    """Scores a proposed call; pure function of call + context."""

    def assess(
        self, task_id: str, tool: Tool, call: ToolCall, context: RiskContext
    ) -> RiskAssessment:
        factors: list[str] = []
        level = tool.risk_level

        def escalate(to: RiskLevel, factor: str) -> None:
            nonlocal level
            factors.append(factor)
            if _LEVEL_ORDER[to] > _LEVEL_ORDER[level]:
                level = to

        def note(factor: str) -> None:
            factors.append(factor)

        name = tool.name.lower()
        if any(word in name for word in ("delete", "remove", "destroy", "rm ")):
            escalate(RiskLevel.CRITICAL, f"destructive tool name: {tool.name}")
        elif any(word in name for word in ("write", "modify", "install", "overwrite")):
            escalate(RiskLevel.HIGH, f"mutating tool name: {tool.name}")

        for key, value in call.arguments.items():
            self._scan_value(value, key, tool, context, escalate, note)

        return RiskAssessment(
            task_id=task_id,
            tool_call_id=call.id,
            level=level,
            factors=factors or [f"base level for {tool.name}"],
            rationale=f"assessed {tool.name} for agent {context.agent_id or 'default'}",
        )

    def _scan_value(
        self, value: object, key: str, tool: Tool, context: RiskContext,
        escalate, note,
    ) -> None:
        """Recurse into lists/tuples/dicts so payloads can't hide.

        Shell metacharacters inside the `command` argv list of
        terminal.execute are passed literally (no shell), so they are
        recorded as a factor without escalating; everywhere else they
        escalate, as does any path traversal at any depth.
        """
        if isinstance(value, str):
            self._scan_string(value, key, tool, context, escalate, note,
                              literal_argv=(tool.name == "terminal.execute"
                                            and key == "command"))
            return
        if isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                if isinstance(item, str):
                    self._scan_string(item, f"{key}[{index}]", tool, context,
                                      escalate, note,
                                      literal_argv=(tool.name == "terminal.execute"
                                                    and key == "command"))
                else:
                    self._scan_value(item, f"{key}[{index}]", tool, context,
                                     escalate, note)
            return
        if isinstance(value, dict):
            for sub_key, item in value.items():
                self._scan_value(item, f"{key}.{sub_key}", tool, context,
                                 escalate, note)

    def _scan_string(
        self, value: str, key: str, tool: Tool, context: RiskContext, escalate,
        note, literal_argv: bool = False,
    ) -> None:
        if ".." in value.replace("\\", "/").split("/"):
            escalate(RiskLevel.CRITICAL, f"path traversal in {key!r}")
            return
        candidate = Path(value)
        if candidate.is_absolute() and context.root:
            try:
                candidate.resolve().relative_to(Path(context.root).resolve())
            except ValueError:
                escalate(
                    RiskLevel.HIGH, f"absolute path outside allowed root in {key!r}"
                )
        is_argv = literal_argv or (tool.name == "terminal.execute"
                                   and key == "command")
        if any(token in value for token in _SHELL_TOKENS):
            if is_argv:
                note(f"shell-like tokens in {key!r} (passed literally, no shell)")
            else:
                escalate(RiskLevel.HIGH, f"shell metacharacters in {key!r}")


class Policy(BaseModel):
    """One named authorization policy."""

    name: str = "default"
    auto_grant_up_to: RiskLevel = RiskLevel.MEDIUM
    deny_critical: bool = True
    denied_tools: set[str] = Field(default_factory=set)
    agent_scopes: dict[str, set[str]] = Field(default_factory=dict)
    strict_agent_scopes: bool = False


class PolicyEngine:
    """Decides grant/deny from an assessment under a policy."""

    def __init__(self, policy: Policy | None = None) -> None:
        self.policy = policy or Policy()

    def evaluate(
        self, assessment: RiskAssessment, tool_name: str, agent_id: str = ""
    ) -> tuple[bool, str]:
        """Return (granted, reason)."""
        policy = self.policy
        if tool_name in policy.denied_tools:
            return False, f"tool {tool_name!r} is denied by policy {policy.name!r}"
        if agent_id and agent_id in policy.agent_scopes:
            if tool_name not in policy.agent_scopes[agent_id]:
                return False, f"tool {tool_name!r} is outside agent {agent_id!r} scope"
        elif policy.strict_agent_scopes and policy.agent_scopes:
            return False, (
                f"agent {agent_id!r} has no scope under strict policy {policy.name!r}"
            )
        if policy.deny_critical and assessment.level is RiskLevel.CRITICAL:
            return False, f"CRITICAL risk denied: {'; '.join(assessment.factors)}"
        if _LEVEL_ORDER[assessment.level] <= _LEVEL_ORDER[policy.auto_grant_up_to]:
            return True, f"risk {assessment.level.value} within {policy.name!r} grant band"
        return False, (
            f"risk {assessment.level.value} exceeds {policy.name!r} grant band "
            f"({policy.auto_grant_up_to.value})"
        )


class PermissionEngine:
    """Records decisions as Permission objects (the audit trail seeds)."""

    def __init__(self, policy_name: str = "default") -> None:
        self.policy_name = policy_name

    def decide(
        self,
        task_id: str,
        call: ToolCall,
        granted: bool,
        reason: str,
    ) -> Permission:
        """Build the permission record for a decision."""
        return Permission(
            task_id=task_id,
            tool_call_id=call.id,
            decision=PermissionDecision.GRANTED if granted else PermissionDecision.DENIED,
            reason=reason,
            policy=self.policy_name,
        )


class AuthorizationManager:
    """The single choke point between intentions and execution.

    Satisfies the runner's ``Authorizer`` protocol. ``authorize`` returns
    a record (the runner maps DENIED to a failed result); ``enforce``
    raises for direct callers that need an exception.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        risk_engine: RiskEngine | None = None,
        policy_engine: PolicyEngine | None = None,
        permission_engine: PermissionEngine | None = None,
        context: RiskContext | None = None,
    ) -> None:
        self._registry = registry
        self._risk = risk_engine or RiskEngine()
        self._policy = policy_engine or PolicyEngine()
        self._permissions = permission_engine or PermissionEngine(
            self._policy.policy.name
        )
        self._context = context or RiskContext()

    def authorize(self, task_id: str, tool: Tool, call: ToolCall) -> Permission:
        """Validate -> risk -> policy -> permission record.

        The contract always comes from the registry: a caller-supplied
        Tool object is never trusted for schema or risk, only its name
        is used to look the authoritative contract up.
        """
        known = self._registry.get(tool.name)
        if known is None:
            return self._permissions.decide(
                task_id, call, False, f"unknown tool {tool.name!r}"
            )
        required = known.input_schema.get("required", [])
        missing = [name for name in required if name not in call.arguments]
        if missing:
            raise DomainValidationError(
                f"missing required arguments: {', '.join(missing)}"
            )
        assessment = self._risk.assess(task_id, known, call, self._context)
        granted, reason = self._policy.evaluate(
            assessment, known.name, self._context.agent_id
        )
        return self._permissions.decide(task_id, call, granted, reason)

    def enforce(
        self, task_id: str, tool: Tool, call: ToolCall, agent_id: str = ""
    ) -> Permission:
        """Like authorize, but raise AuthorizationDeniedError on denial."""
        known = self._registry.get(tool.name)
        if known is None:
            raise AuthorizationDeniedError(
                task_id, tool.name, f"unknown tool {tool.name!r}")
        context = self._context if not agent_id else RiskContext(
            agent_id=agent_id,
            environment=self._context.environment,
            root=self._context.root,
        )
        assessment = self._risk.assess(task_id, known, call, context)
        granted, reason = self._policy.evaluate(assessment, known.name, agent_id)
        permission = self._permissions.decide(task_id, call, granted, reason)
        if not granted:
            raise AuthorizationDeniedError(task_id, known.name, reason)
        return permission

    @property
    def policy(self) -> Policy:
        """Active policy (read-only view)."""
        return self._policy.policy
