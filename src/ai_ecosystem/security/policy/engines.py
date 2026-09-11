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
from typing import Any

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
from ai_ecosystem.core.models.enums import (
    ApprovalStatus,
    PermissionDecision,
    RiskLevel,
)
from ai_ecosystem.tools.registry.registry import ToolRegistry
from ai_ecosystem.tools.registry.validation import check_arguments

_LEVEL_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

_SHELL_TOKENS = (";", "&&", "||", "$(", "`", "|")

#: Executables that interpret their arguments as a scripting language.
#: Launched through the argv API they still hand the model a full
#: shell, so they are CRITICAL unless the operator opted in.
SHELL_BINARIES = frozenset({
    "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe",
    "bash", "sh", "zsh", "fish", "dash", "wsl", "wsl.exe",
    "cscript", "cscript.exe", "wscript", "wscript.exe",
    "mshta", "mshta.exe", "rundll32", "rundll32.exe",
})

#: Destructive command patterns (review: classify the COMMAND, not just
#: the tool). Matched case-insensitively against the joined argv of
#: subprocess tools. Each hit escalates to CRITICAL: these operations
#: destroy data, exfiltrate, or escape containment regardless of which
#: binary spells them.
DESTRUCTIVE_PATTERNS = (
    "del /s", "rd /s", "rmdir /s", "format.com", "format c:", "format d:",
    "rm -rf /", "rm -rf ~", "rm -rf $home", "mkfs", "dd if=",
    ":(){:|:&};:",
    "powershell -enc", "powershell -encodedcommand", "pwsh -enc",
    "-encodedcommand", "invoke-expression", "iex(",
    "curl ", "wget ", "| powershell", "| pwsh", "| sh", "| bash",
    "git push --force", "git push -f", "git reset --hard",
    "git clean -fd", "git clean -fdx",
    "reg delete", "takeown", "cipher /w", "vssadmin delete",
    "bcdedit", "diskpart",
)


class RiskContext(BaseModel):
    """Everything about *who/where* a call runs, besides the call itself."""

    agent_id: str = ""
    environment: str = "local"
    root: str = ""


class RiskEngine:
    """Scores a proposed call; pure function of call + context."""

    def __init__(self, allow_shells: bool = False) -> None:
        self._allow_shells = allow_shells

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

        if "subprocess" in (tool.capabilities or []):
            self._assess_executable(call, escalate, note)
            self._assess_command(call, escalate)

        for key, value in call.arguments.items():
            self._scan_value(value, key, tool, context, escalate, note)

        return RiskAssessment(
            task_id=task_id,
            tool_call_id=call.id,
            level=level,
            factors=factors or [f"base level for {tool.name}"],
            rationale=f"assessed {tool.name} for agent {context.agent_id or 'default'}",
        )

    def _assess_executable(self, call: ToolCall, escalate, note) -> None:
        """Shell interpreters are a separate capability, not argv data.

        ``terminal.execute`` never invokes a shell itself, but launching
        cmd/powershell/bash *through* it hands the model one anyway.
        Blocked (CRITICAL) unless the operator opted in.
        """
        import os

        command = call.arguments.get("command")
        if not isinstance(command, list) or not command:
            return
        first = command[0]
        if not isinstance(first, str):
            return
        binary = os.path.basename(first).lower()
        if binary not in SHELL_BINARIES:
            return
        if self._allow_shells:
            note(f"shell interpreter {first!r} explicitly allowed by operator")
        else:
            escalate(RiskLevel.CRITICAL,
                      f"shell interpreter {first!r} requires explicit "
                      "operator opt-in")

    def _assess_command(self, call: ToolCall, escalate) -> None:
        """Classify the actual command, not just the tool name.

        ``terminal.execute`` with ``["python", "--version"]`` and with
        ``["cmd", "/c", "del /s /q *"]`` are different universes of
        risk. Known-destructive patterns escalate to CRITICAL even
        when the binary itself is innocuous.
        """
        command = call.arguments.get("command")
        if not isinstance(command, list) or not command:
            return
        text = " ".join(part for part in command if isinstance(part, str))
        lowered = f" {text.lower()} "
        for pattern in DESTRUCTIVE_PATTERNS:
            if pattern in lowered:
                escalate(RiskLevel.CRITICAL,
                         f"destructive command pattern {pattern!r}")
                return

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
    # Human-approval floor (review P0): None disables the subsystem
    # entirely (previous behavior); otherwise this risk level and up
    # -- plus any tool flagged requires_approval -- needs a human
    # decision bound to the exact arguments before executing.
    approval_required_from: RiskLevel | None = None
    approval_ttl_s: float = 900.0


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
        allow_shells: bool = False,
        approval_store: Any = None,
        approval_wait_s: float = 300.0,
    ) -> None:
        self._registry = registry
        self._risk = risk_engine or RiskEngine(allow_shells=allow_shells)
        self._policy = policy_engine or PolicyEngine()
        self._permissions = permission_engine or PermissionEngine(
            self._policy.policy.name
        )
        self._context = context or RiskContext()
        self._approvals = approval_store
        self._approval_wait_s = max(1.0, approval_wait_s)

    def _approval_floor(self) -> RiskLevel | None:
        return self._policy.policy.approval_required_from

    def _needs_approval(self, tool: Tool, level: RiskLevel) -> bool:
        """True when a human must decide before this call may run."""
        if tool.requires_approval:
            return self._approval_floor() is not None
        floor = self._approval_floor()
        return floor is not None and _LEVEL_ORDER[level] >= _LEVEL_ORDER[floor]

    def _decide(self, task_id: str, tool: Tool, call: ToolCall,
                agent_id: str = "") -> tuple[Tool, Permission]:
        """The ONE authorization pipeline (review fix 02/11).

        Both authorize() and enforce() run exactly this: registry
        contract lookup -> canonical argument validation -> risk ->
        policy -> permission record. No second path, no drift.

        When the policy arms human approval for this call, an existing
        valid APPROVED request grants immediately; otherwise a PENDING
        request is created (or reused) and the caller must wait for a
        human via await_approval().
        """
        known = self._registry.get(tool.name)
        if known is None:
            return tool, self._permissions.decide(
                task_id, call, False, f"unknown tool {tool.name!r}")
        if call.task_id != task_id:
            return known, self._permissions.decide(
                task_id, call, False,
                "task_id mismatch between call and request")
        problems = check_arguments(known, dict(call.arguments))
        if problems:
            raise DomainValidationError("; ".join(problems))
        context = self._context
        if agent_id:
            context = RiskContext(
                agent_id=agent_id,
                environment=self._context.environment,
                root=self._context.root)
        assessment = self._risk.assess(task_id, known, call, context)
        if self._needs_approval(known, assessment.level):
            return known, self._approval_gate(
                task_id, known, call, assessment)
        granted, reason = self._policy.evaluate(
            assessment, known.name, agent_id or self._context.agent_id)
        return known, self._permissions.decide(task_id, call, granted, reason)

    def _approval_gate(self, task_id: str, tool: Tool, call: ToolCall,
                       assessment: RiskAssessment) -> Permission:
        """Approval branch: reuse a valid grant or open a PENDING request."""

        policy_name = self._policy.policy.name
        if self._approvals is None:
            return self._permissions.decide(
                task_id, call, False,
                f"tool {tool.name!r} requires human approval, but no "
                "approval store is configured")
        valid = self._approvals.find_valid(
            task_id, tool.name, dict(call.arguments), policy_name)
        if valid is not None:
            granted = self._permissions.decide(
                task_id, call, True,
                f"human-approved action {valid.id[:8]} "
                f"({assessment.level.value} risk)")
            granted.approval_id = valid.id
            return granted
        ttl = self._policy.policy.approval_ttl_s
        request = self._approvals.request(
            task_id, tool.name, dict(call.arguments),
            assessment.level.value,
            f"{assessment.level.value} risk: {'; '.join(assessment.factors)}",
            policy_name, ttl_s=ttl)
        pending = self._permissions.decide(
            task_id, call, False,
            f"waiting for human approval {request.id[:8]} "
            f"({assessment.level.value} risk)")
        pending.decision = PermissionDecision.PENDING
        pending.approval_id = request.id
        return pending

    def await_approval(self, permission: Permission,
                       timeout_s: float | None = None) -> Permission:
        """Block until the human decides a PENDING permission (runner path).

        Returns GRANTED (hash-bound to the approval) or DENIED. Never
        raises for undecided/timeout: waiting too long denies safely.
        """
        import time as _time

        if permission.decision is not PermissionDecision.PENDING:
            return permission
        if self._approvals is None or not permission.approval_id:
            permission.decision = PermissionDecision.DENIED
            permission.reason += " (no approval store to wait on)"
            return permission
        deadline = _time.monotonic() + min(
            max(1.0, timeout_s if timeout_s is not None
                else self._approval_wait_s), 3600.0)
        while _time.monotonic() < deadline:
            request = self._approvals.get(permission.approval_id)
            if request is None:
                break
            self._approvals.sweep_expired()
            request = self._approvals.get(permission.approval_id)
            if request is None:
                break
            if request.status is ApprovalStatus.APPROVED:
                permission.decision = PermissionDecision.GRANTED
                permission.reason = (
                    f"human approved action {request.id[:8]}")
                return permission
            if request.status in (ApprovalStatus.DENIED,
                                  ApprovalStatus.EXPIRED):
                permission.decision = PermissionDecision.DENIED
                permission.reason = (
                    f"human {request.status.value.lower()} action "
                    f"{request.id[:8]}")
                return permission
            _time.sleep(0.2)
        permission.decision = PermissionDecision.DENIED
        permission.reason += " (approval wait timed out)"
        return permission

    def check_approval(self, permission: Permission, tool: Tool,
                       call: ToolCall) -> bool:
        """Re-verify the hash binding against the LIVE call arguments."""

        if not permission.approval_id or self._approvals is None:
            return False
        return self._approvals.verify(
            permission.approval_id, tool.name, dict(call.arguments),
            self._policy.policy.name)

    def authorize(self, task_id: str, tool: Tool, call: ToolCall) -> Permission:
        """Validate -> risk -> policy -> permission record.

        The contract always comes from the registry: a caller-supplied
        Tool object is never trusted for schema or risk, only its name
        is used to look the authoritative contract up.
        """
        _, permission = self._decide(
            task_id, tool, call, self._context.agent_id)
        return permission

    def enforce(
        self, task_id: str, tool: Tool, call: ToolCall, agent_id: str = ""
    ) -> Permission:
        """Like authorize, but raise AuthorizationDeniedError on denial."""
        _, permission = self._decide(task_id, tool, call, agent_id)
        if permission.decision is not PermissionDecision.GRANTED:
            known = self._registry.get(tool.name)
            raise AuthorizationDeniedError(
                task_id, known.name if known else tool.name,
                permission.reason)
        return permission

    @property
    def policy(self) -> Policy:
        """Active policy (read-only view)."""
        return self._policy.policy
