"""Policy-checked tool invocation with durable execution identity."""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, Protocol

from ai_ecosystem.core.errors.exceptions import (
    AuthorizationDeniedError,
    DomainValidationError,
    ToolError,
    ToolTimeoutError,
)
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.domain import Permission, Tool, ToolCall, ToolResult
from ai_ecosystem.core.models.enums import EventType, PermissionDecision, ToolCallStatus
from ai_ecosystem.core.secrets import looks_like_secret_value, redact
from ai_ecosystem.security.sandbox import LocalSandboxProvider, SandboxProfile
from ai_ecosystem.tools.registry.execution_ledger import (
    ExecutionLedger,
    ExecutionState,
    action_hash,
)
from ai_ecosystem.tools.registry.registry import ToolRegistry
from ai_ecosystem.tools.registry.validation import check_arguments


def _safe_payload(arguments: dict) -> dict:
    try:
        return redact({str(key): value for key, value in arguments.items()})
    except Exception:
        return {"redacted": True}


def _safe_error(error: str) -> str:
    return "***" if looks_like_secret_value(error) else error


def _safe_output_snippet(output: Any, limit: int = 500) -> str:
    if output is None:
        return ""
    if isinstance(output, dict):
        output = redact(output)
    text = output if isinstance(output, str) else repr(output)
    if looks_like_secret_value(text):
        return "***"
    return text if len(text) <= limit else text[:limit] + f"… [{len(text) - limit} more chars]"


def _run_with_deadline(handler: Any, arguments: dict, timeout_s: float) -> Any:
    """Deadline for trusted non-sandboxed handlers.

    Untrusted OS/process tools must be sandboxed; a Python thread cannot be
    force-killed safely, so the timeout here is only a deadline for trusted
    handlers and never claims to terminate them.
    """
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = handler(arguments)
        except BaseException as exc:
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True, name="tool-runner")
    thread.start()
    thread.join(max(float(timeout_s), 0.0))
    if thread.is_alive():
        raise FuturesTimeoutError(f"handler exceeded {timeout_s}s deadline")
    if "error" in box:
        raise box["error"]
    return box.get("result")


class Authorizer(Protocol):
    def authorize(self, task_id: str, tool: Tool, call: ToolCall) -> Permission: ...


class GrantAllAuthorizer:
    def authorize(self, task_id: str, tool: Tool, call: ToolCall) -> Permission:
        return Permission(
            task_id=task_id,
            tool_call_id=call.id,
            decision=PermissionDecision.GRANTED,
            reason="grant-all (tests only)",
            policy="grant-all",
        )


class DenyAllAuthorizer:
    def authorize(self, task_id: str, tool: Tool, call: ToolCall) -> Permission:
        return Permission(
            task_id=task_id,
            tool_call_id=call.id,
            decision=PermissionDecision.DENIED,
            reason="deny-all (tests only)",
            policy="deny-all",
        )


class ToolRunner:
    """Runs validation -> authorization -> durable identity -> execution."""

    def __init__(
        self,
        registry: ToolRegistry,
        authorizer: Authorizer,
        bus: EventBus | None = None,
        sandbox: Any = None,
        sandbox_profiles: dict[str, Any] | None = None,
        auditor: Any = None,
        execution_ledger: ExecutionLedger | None = None,
    ) -> None:
        self._registry = registry
        self._authorizer = authorizer
        self._bus = bus
        self._sandbox = sandbox if sandbox is not None else LocalSandboxProvider()
        self._sandbox_profiles = dict(sandbox_profiles or {})
        self._auditor = auditor
        self._ledger = execution_ledger
        self._seen_ids: set[str] = set()
        self._seen_lock = threading.Lock()

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(Event(event_type=event_type, task_id=task_id, payload=payload))

    def _fail(
        self,
        call: ToolCall,
        event_type: EventType,
        error: str,
        tool: Tool | None = None,
        permission: Permission | None = None,
    ) -> ToolResult:
        safe_error = _safe_error(error)
        call.status = ToolCallStatus.FAILED
        self._emit(
            event_type, call.task_id, {"tool": call.tool, "call_id": call.id, "error": safe_error}
        )
        self._audit(call, tool or call.tool, permission, False, safe_error)
        return ToolResult(
            task_id=call.task_id, tool_call_id=call.id, success=False, error=safe_error
        )

    def _validate(self, tool: Tool, call: ToolCall) -> str | None:
        problems = check_arguments(tool, dict(call.arguments))
        return "; ".join(problems) if problems else None

    def _deny(self, call: ToolCall, tool: Tool, reason: str) -> ToolResult:
        safe_reason = _safe_error(reason)
        call.status = ToolCallStatus.DENIED
        self._emit(
            EventType.PERMISSION_DENIED,
            call.task_id,
            {"tool": call.tool, "call_id": call.id, "reason": safe_reason},
        )
        self._audit(call, tool, None, False, safe_reason)
        return ToolResult(
            task_id=call.task_id, tool_call_id=call.id, success=False, error=safe_reason
        )

    def _await_human(
        self, call: ToolCall, tool: Tool, permission: Permission, cancel_token: Any = None
    ) -> Permission:
        approval_id = getattr(permission, "approval_id", "") or ""
        self._emit(
            EventType.APPROVAL_REQUESTED,
            call.task_id,
            {
                "tool": call.tool,
                "call_id": call.id,
                "approval_id": approval_id,
                "reason": permission.reason,
            },
        )
        waiter = getattr(self._authorizer, "await_approval", None)
        if not callable(waiter):
            permission.decision = PermissionDecision.DENIED
            permission.reason += " (authorizer cannot wait for approval)"
        else:
            permission = waiter(permission, cancelled=cancel_token)
        if cancel_token is not None and getattr(cancel_token, "cancelled", False):
            permission.decision = PermissionDecision.DENIED
            permission.reason = "task cancelled while awaiting approval"
        self._emit(
            EventType.APPROVAL_DECIDED,
            call.task_id,
            {
                "tool": call.tool,
                "call_id": call.id,
                "approval_id": approval_id,
                "decision": permission.decision.value,
            },
        )
        if permission.decision is PermissionDecision.GRANTED:
            checker = getattr(self._authorizer, "check_approval", None)
            if not callable(checker) or not checker(permission, tool, call):
                permission.decision = PermissionDecision.DENIED
                permission.reason = "approval does not match this exact action"
        return permission

    def _reserve_action(self, call: ToolCall) -> tuple[bool, str]:
        """Reserve exact semantic action; model-generated call IDs are not identity."""
        digest = action_hash(call.task_id, call.tool, dict(call.arguments))
        if self._ledger is not None:
            record = self._ledger.get(digest)
            if record is not None:
                return (
                    False,
                    f"action already has durable state {record.state.value}; reconcile before replay",
                )
            self._ledger.begin(call.task_id, call.tool, dict(call.arguments))
        with self._seen_lock:
            if call.id in self._seen_ids:
                return False, f"duplicate call id {call.id!r}: already executed"
            self._seen_ids.add(call.id)
        return True, digest

    def run(self, call: ToolCall, cancel_token: Any = None) -> ToolResult:
        if cancel_token is not None and getattr(cancel_token, "cancelled", False):
            call.status = ToolCallStatus.FAILED
            return ToolResult(
                task_id=call.task_id,
                tool_call_id=call.id,
                success=False,
                error="cancelled before execution",
            )
        tool = self._registry.get(call.tool)
        if tool is None:
            return self._fail(call, EventType.TOOL_FAILED, f"unknown tool {call.tool!r}")
        self._emit(
            EventType.TOOL_REQUESTED,
            call.task_id,
            {
                "tool": call.tool,
                "call_id": call.id,
                "arguments": _safe_payload(dict(call.arguments)),
            },
        )
        problem = self._validate(tool, call)
        if problem is not None:
            return self._fail(call, EventType.TOOL_FAILED, problem, tool)
        call.status = ToolCallStatus.VALIDATED
        try:
            permission = self._authorizer.authorize(call.task_id, tool, call)
        except (AuthorizationDeniedError, DomainValidationError) as exc:
            return self._deny(call, tool, str(exc))
        except Exception as exc:
            return self._deny(call, tool, f"authorizer error: {exc}")
        if permission.decision is PermissionDecision.PENDING:
            try:
                permission = self._await_human(call, tool, permission, cancel_token)
            except Exception as exc:
                return self._deny(call, tool, f"approval wait failed: {exc}")
        if permission.decision is not PermissionDecision.GRANTED:
            return self._deny(call, tool, f"denied: {permission.reason}")
        if cancel_token is not None and getattr(cancel_token, "cancelled", False):
            call.status = ToolCallStatus.DENIED
            return ToolResult(
                task_id=call.task_id,
                tool_call_id=call.id,
                success=False,
                error="cancelled before execution",
            )
        ok, identity = self._reserve_action(call)
        if not ok:
            return self._deny(call, tool, identity)
        self._emit(
            EventType.PERMISSION_GRANTED,
            call.task_id,
            {
                "tool": call.tool,
                "call_id": call.id,
                "approval_id": getattr(permission, "approval_id", "") or "",
                "action_hash": identity,
            },
        )
        call.status = ToolCallStatus.EXECUTE
        self._emit(
            EventType.TOOL_STARTED,
            call.task_id,
            {"tool": call.tool, "call_id": call.id, "action_hash": identity},
        )
        handler = self._registry.handler(call.tool)
        assert handler is not None
        if tool.requires_sandbox:
            return self._run_sandboxed(call, tool, handler, permission, cancel_token, identity)
        try:
            result = _run_with_deadline(handler, dict(call.arguments), tool.timeout_s)
        except FuturesTimeoutError:
            return self._fail(
                call,
                EventType.TOOL_FAILED,
                str(ToolTimeoutError(call.tool, tool.timeout_s)),
                tool,
                permission,
            )
        except (ToolError, DomainValidationError) as exc:
            return self._fail(call, EventType.TOOL_FAILED, str(exc), tool, permission)
        except Exception as exc:
            return self._fail(
                call, EventType.TOOL_FAILED, f"handler crashed: {exc}", tool, permission
            )
        return self._finish(call, tool, permission, result, identity)

    def _run_sandboxed(
        self,
        call: ToolCall,
        tool: Tool,
        handler: Any,
        permission: Permission,
        cancel_token: Any = None,
        identity: str = "",
    ) -> ToolResult:
        profile = self._sandbox_profiles.get(tool.sandbox_profile)
        if profile is None:
            if tool.sandbox_profile == "terminal":
                profile = SandboxProfile(
                    name="terminal",
                    fs_root="",
                    allow_network=False,
                    timeout_s=tool.timeout_s,
                    max_processes=1,
                )
            else:
                return self._fail(
                    call,
                    EventType.TOOL_FAILED,
                    f"tool {call.tool!r} requires unknown sandbox profile {tool.sandbox_profile!r}",
                    tool,
                    permission,
                )
        try:
            result = self._sandbox.run(
                tool,
                handler,
                dict(call.arguments),
                profile,
                tool.timeout_s,
                cancel_token=cancel_token,
            )
        except (ToolError, DomainValidationError) as exc:
            return self._fail(call, EventType.TOOL_FAILED, str(exc), tool, permission)
        except Exception as exc:
            return self._fail(
                call, EventType.TOOL_FAILED, f"sandbox crashed: {exc}", tool, permission
            )
        return self._finish(call, tool, permission, result, sandboxed=True, identity=identity)

    def _finish(
        self,
        call: ToolCall,
        tool: Tool,
        permission: Permission,
        result: Any,
        identity: str = "",
        sandboxed: bool = False,
    ) -> ToolResult:
        if not isinstance(result, ToolResult):
            return self._fail(
                call,
                EventType.TOOL_FAILED,
                f"handler for {call.tool!r} returned {type(result).__name__}",
                tool,
                permission,
            )
        result.task_id = call.task_id
        result.tool_call_id = call.id
        call.status = ToolCallStatus.COMPLETED if result.success else ToolCallStatus.FAILED
        event = EventType.TOOL_COMPLETED if result.success else EventType.TOOL_FAILED
        self._emit(
            event,
            call.task_id,
            {
                "tool": call.tool,
                "call_id": call.id,
                "action_hash": identity,
                "output_snippet": _safe_output_snippet(result.output),
                "error": _safe_error(result.error or ""),
            },
        )
        if self._ledger is not None and identity:
            digest = hashlib.sha256(repr(result.output).encode("utf-8", "replace")).hexdigest()
            self._ledger.transition(
                identity,
                ExecutionState.COMPLETED if result.success else ExecutionState.FAILED,
                digest,
            )
        self._audit(
            call,
            tool,
            permission,
            result.success,
            result.error or "",
            sandboxed=sandboxed,
            action_hash=identity,
        )
        return result

    def _audit(
        self,
        call: ToolCall,
        tool: Any,
        permission: Any,
        success: bool,
        error: str,
        sandboxed: bool = False,
        action_hash: str = "",
    ) -> None:
        if self._auditor is None:
            return
        try:
            name = tool.name if isinstance(tool, Tool) else str(tool)
            risk = tool.risk_level.value if isinstance(tool, Tool) else "UNKNOWN"
            decision = getattr(permission, "decision", None)
            granted = getattr(decision, "value", None) == "GRANTED"
            self._auditor(
                {
                    "action": "tool.execute",
                    "task_id": call.task_id,
                    "tool": name,
                    "call_id": call.id,
                    "action_hash": action_hash,
                    "decision": "GRANTED" if granted else "DENIED",
                    "risk": risk,
                    "reason": permission.reason if permission is not None else error,
                    "success": success,
                    "error": error,
                    "sandboxed": sandboxed,
                }
            )
        except Exception:
            pass
