"""Policy-checked tool invocation (Gate 5 lifecycle seam).

Flow per call::

    REQUESTED -> VALIDATED -> (authorizer) -> EXECUTE -> OBSERVE -> result
                                   |
                                 DENIED -> failed result, handler never runs

The ``authorizer`` is a structural protocol: anything with
``authorize(task_id, tool, call) -> Permission`` qualifies. Gate 6
provides the real :class:`AuthorizationManager`; until then tests inject
stubs. Handler exceptions become failed results -- they never propagate
as raw tracebacks to callers.
"""

from __future__ import annotations

from collections.abc import Callable
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
from ai_ecosystem.core.models.enums import (
    EventType,
    PermissionDecision,
    ToolCallStatus,
)
from ai_ecosystem.core.secrets import looks_like_secret_value, redact
from ai_ecosystem.tools.registry.registry import ToolRegistry
from ai_ecosystem.tools.registry.validation import check_arguments


def _safe_payload(arguments: dict) -> dict:
    """Redacted copy of call arguments for events (never raw secrets)."""
    try:
        return redact({str(key): value for key, value in arguments.items()})
    except Exception:  # noqa: BLE001 -- redaction degrades, never fails
        return {"redacted": True}


def _safe_error(error: str) -> str:
    """Mask error text that itself matches a credential format."""
    if looks_like_secret_value(error):
        return "***"
    return error


def _safe_output_snippet(output: Any, limit: int = 500) -> str:
    """Short UI-safe preview of a tool output (redacted, capped).

    The full output stays runtime-side; the event trail carries only
    this preview so the UI can show what happened without bloating
    the store or leaking credentials.
    """
    if output is None:
        return ""
    if isinstance(output, dict):
        output = redact(output)
    text = output if isinstance(output, str) else repr(output)
    if looks_like_secret_value(text):
        return "***"
    if len(text) > limit:
        return text[:limit] + f"… [{len(text) - limit} more chars]"
    return text


def _run_with_deadline(handler: Any, arguments: dict, timeout_s: float) -> Any:
    """Run a handler with a hard deadline that never blocks the caller.

    The worker is a daemon thread, so an overrunning handler cannot hang
    graph shutdown or interpreter exit; its late result is discarded.
    Handler exceptions propagate to the caller unchanged.
    """
    import threading

    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = handler(arguments)
        except BaseException as exc:  # noqa: BLE001 -- re-raised below
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
    """Anything that can approve or refuse a tool call."""

    def authorize(self, task_id: str, tool: Tool, call: ToolCall) -> Permission:
        """Return a GRANTED or DENIED permission record."""
        ...  # pragma: no cover


class GrantAllAuthorizer:
    """Test/utility authorizer that approves everything (never production)."""

    def authorize(self, task_id: str, tool: Tool, call: ToolCall) -> Permission:
        return Permission(
            task_id=task_id,
            tool_call_id=call.id,
            decision=PermissionDecision.GRANTED,
            reason="grant-all (tests only)",
            policy="grant-all",
        )


class DenyAllAuthorizer:
    """Test authorizer that refuses everything."""

    def authorize(self, task_id: str, tool: Tool, call: ToolCall) -> Permission:
        return Permission(
            task_id=task_id,
            tool_call_id=call.id,
            decision=PermissionDecision.DENIED,
            reason="deny-all (tests only)",
            policy="deny-all",
        )


class ToolRunner:
    """Runs tool calls through validation -> authorization -> execution.

    Optional hardening (all default off, fully backward compatible):

    * ``sandbox`` + ``sandbox_profiles``: tools flagged requires_sandbox
      execute inside the provider and fail closed without one;
    * ``auditor``: callable receiving an audit dict per terminal outcome
      (granted/denied/completed/failed) for the Gate 31 trail.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        authorizer: Authorizer,
        bus: EventBus | None = None,
        sandbox: Any = None,
        sandbox_profiles: dict[str, Any] | None = None,
        auditor: Callable[[dict], None] | None = None,
    ) -> None:
        self._registry = registry
        self._authorizer = authorizer
        self._bus = bus
        self._sandbox = sandbox
        self._sandbox_profiles = dict(sandbox_profiles or {})
        self._auditor = auditor

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(event_type=event_type, task_id=task_id, payload=payload)
            )

    def _fail(
        self,
        call: ToolCall,
        event_type: EventType,
        error: str,
        tool: Tool | None = None,
        permission: Permission | None = None,
    ) -> ToolResult:
        error = _safe_error(error)
        call.status = ToolCallStatus.FAILED
        self._emit(
            event_type,
            call.task_id,
            {"tool": call.tool, "call_id": call.id, "error": error},
        )
        # Failures are security-relevant (unknown tools, validation,
        # timeouts, crashes): they join the audit trail like denials.
        self._audit(call, tool or call.tool, permission, False, error)
        return ToolResult(
            task_id=call.task_id, tool_call_id=call.id, success=False, error=error
        )

    def _validate(self, tool: Tool, call: ToolCall) -> str | None:
        problems = check_arguments(tool, dict(call.arguments))
        if problems:
            return "; ".join(problems)
        return None

    def _deny(self, call: ToolCall, tool: Tool, reason: str) -> ToolResult:
        """Shared denial path: record, emit, audit, failed result."""
        reason = _safe_error(reason)
        call.status = ToolCallStatus.DENIED
        self._emit(
            EventType.PERMISSION_DENIED,
            call.task_id,
            {"tool": call.tool, "call_id": call.id, "reason": reason},
        )
        self._audit(call, tool, None, False, reason)
        return ToolResult(
            task_id=call.task_id,
            tool_call_id=call.id,
            success=False,
            error=reason,
        )

    def run(self, call: ToolCall, cancel_token: Any = None) -> ToolResult:
        """Execute one call; always returns a ToolResult (never raises).

        A cancelled token fails fast before touching the handler: steps
        cancelled while queued never start work.
        """
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
            return self._fail(
                call, EventType.TOOL_FAILED, f"unknown tool {call.tool!r}"
            )
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
        except AuthorizationDeniedError as exc:
            return self._deny(call, tool, str(exc))
        except DomainValidationError as exc:
            # Malformed calls (missing args) are refusals, not crashes:
            # the runner's never-raises contract covers authorizers too.
            return self._deny(call, tool, str(exc))
        except Exception as exc:  # noqa: BLE001 -- fail closed, never propagate
            return self._deny(call, tool, f"authorizer error: {exc}")
        if permission.decision is not PermissionDecision.GRANTED:
            call.status = ToolCallStatus.DENIED
            self._emit(
                EventType.PERMISSION_DENIED,
                call.task_id,
                {
                    "tool": call.tool,
                    "call_id": call.id,
                    "reason": permission.reason,
                },
            )
            self._audit(call, tool, permission, False, f"denied: {permission.reason}")
            return ToolResult(
                task_id=call.task_id,
                tool_call_id=call.id,
                success=False,
                error=f"denied: {permission.reason}",
            )

        call.status = ToolCallStatus.EXECUTE
        self._emit(
            EventType.TOOL_STARTED,
            call.task_id,
            {"tool": call.tool, "call_id": call.id},
        )
        handler = self._registry.handler(call.tool)
        assert handler is not None  # registry guarantees the pair
        if tool.requires_sandbox:
            return self._run_sandboxed(call, tool, handler, permission)
        try:
            result = _run_with_deadline(handler, dict(call.arguments), tool.timeout_s)
        except FuturesTimeoutError:
            timeout = ToolTimeoutError(call.tool, tool.timeout_s)
            return self._fail(call, EventType.TOOL_FAILED, str(timeout))
        except ToolError as exc:
            return self._fail(call, EventType.TOOL_FAILED, str(exc))
        except DomainValidationError as exc:
            return self._fail(call, EventType.TOOL_FAILED, str(exc))
        except Exception as exc:  # noqa: BLE001 -- isolate handler bugs
            return self._fail(call, EventType.TOOL_FAILED, f"handler crashed: {exc}")

        if not isinstance(result, ToolResult):
            return self._fail(
                call,
                EventType.TOOL_FAILED,
                f"handler for {call.tool!r} returned {type(result).__name__}",
            )
        result.task_id = call.task_id
        result.tool_call_id = call.id
        call.status = (
            ToolCallStatus.COMPLETED if result.success else ToolCallStatus.FAILED
        )
        self._emit(
            EventType.TOOL_COMPLETED if result.success else EventType.TOOL_FAILED,
            call.task_id,
            {
                "tool": call.tool,
                "call_id": call.id,
                "output_snippet": _safe_output_snippet(result.output),
                "error": _safe_error(result.error or ""),
            },
        )
        self._audit(call, tool, permission, result.success, result.error or "")
        return result

    def _run_sandboxed(
        self, call: ToolCall, tool: Tool, handler: Any, permission: Permission
    ) -> ToolResult:
        """Sandboxed branch: fail closed without a provider, audit always."""

        if self._sandbox is None:
            error = (
                f"tool {call.tool!r} requires sandbox "
                f"{tool.sandbox_profile!r}: no provider configured"
            )
            call.status = ToolCallStatus.FAILED
            self._emit(
                EventType.TOOL_FAILED,
                call.task_id,
                {"tool": call.tool, "call_id": call.id, "error": error},
            )
            self._audit(call, tool, permission, False, error)
            return ToolResult(
                task_id=call.task_id, tool_call_id=call.id, success=False, error=error
            )
        profile = self._sandbox_profiles.get(tool.sandbox_profile)
        if profile is None:
            error = (
                f"tool {call.tool!r} requires unknown sandbox profile "
                f"{tool.sandbox_profile!r}"
            )
            return self._fail(call, EventType.TOOL_FAILED, error)
        try:
            result = self._sandbox.run(
                tool, handler, dict(call.arguments), profile, tool.timeout_s
            )
        except ToolError as exc:
            return self._fail(call, EventType.TOOL_FAILED, str(exc))
        except DomainValidationError as exc:
            return self._fail(call, EventType.TOOL_FAILED, str(exc))
        except Exception as exc:  # noqa: BLE001 -- isolate sandbox bugs
            return self._fail(call, EventType.TOOL_FAILED, f"sandbox crashed: {exc}")
        if not isinstance(result, ToolResult):
            return self._fail(
                call, EventType.TOOL_FAILED, "sandbox returned a non-result"
            )
        result.task_id = call.task_id
        result.tool_call_id = call.id
        call.status = (
            ToolCallStatus.COMPLETED if result.success else ToolCallStatus.FAILED
        )
        self._emit(
            EventType.TOOL_COMPLETED if result.success else EventType.TOOL_FAILED,
            call.task_id,
            {"tool": call.tool, "call_id": call.id},
        )
        self._audit(
            call, tool, permission, result.success, result.error or "", sandboxed=True
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
                    "decision": "GRANTED" if granted else "DENIED",
                    "risk": risk,
                    "reason": (permission.reason if permission is not None else error),
                    "success": success,
                    "error": error,
                    "sandboxed": sandboxed,
                }
            )
        except Exception:  # noqa: BLE001 -- auditing never breaks execution
            pass
