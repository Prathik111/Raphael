"""terminal.execute: run a command argv without a shell, with a deadline.

The contract takes an argv *list* -- strings containing shell metachars
are passed as literal arguments, never interpreted. HIGH risk by default
so the policy layer (Gate 6) denies it unless explicitly allowed.

Containment: the child gets a scrubbed environment (no credentials),
no stdin, a caller timeout clamped to the contract, and a working
directory jailed under ``root`` when one is configured. The child is
killed (not orphaned) when the deadline fires.
"""

from __future__ import annotations

import os
import subprocess

from ai_ecosystem.core.errors.exceptions import ToolExecutionError, ToolTimeoutError
from ai_ecosystem.core.models.domain import Tool, ToolResult
from ai_ecosystem.core.models.enums import RiskLevel
from ai_ecosystem.core.secrets import redact

OUTPUT_CAP = 100_000
#: Contract timeout mirror: the handler never outlives this even when the
#: caller asks for more (the runner enforces the live contract value too).
CONTRACT_TIMEOUT_S = 120.0


def _scrubbed_env() -> dict[str, str]:
    """Process environment with every credential masked or dropped.

    Secret-smelling keys and known credential formats become "***";
    entries that redact to "***" are dropped entirely so the child
    cannot even see that a credential existed.
    """
    redacted = redact(dict(os.environ))
    return {key: value for key, value in redacted.items() if value != "***"}


def _resolve_cwd(raw: object, root: object) -> str | None:
    """Validate the requested cwd; jail it under root when configured."""
    from pathlib import Path

    if raw is None:
        return str(root) if root is not None else None
    if not isinstance(raw, str):
        raise ToolExecutionError(
            "terminal.execute", "'cwd' must be an existing directory")
    candidate = Path(raw)
    if not candidate.is_dir():
        raise ToolExecutionError(
            "terminal.execute", "'cwd' must be an existing directory")
    if root is not None:
        resolved = candidate.resolve()
        try:
            resolved.relative_to(Path(root).resolve())
        except ValueError:
            raise ToolExecutionError(
                "terminal.execute", "'cwd' escapes the allowed root"
            ) from None
        return str(resolved)
    return raw


def _build_execute(root: object):
    """Handler bound to a workspace root (cwd jail + default cwd)."""

    def run(arguments: dict) -> ToolResult:
        return _run(arguments, root)

    return run


def _run(arguments: dict, root: object) -> ToolResult:
    command = arguments.get("command")
    if not isinstance(command, list) or not command:
        raise ToolExecutionError(
            "terminal.execute", "'command' must be a non-empty argv list"
        )
    if any(not isinstance(part, str) for part in command):
        raise ToolExecutionError(
            "terminal.execute", "every argv element must be a string"
        )
    try:
        timeout_s = float(arguments.get("timeout_s", 60.0))
    except (TypeError, ValueError):
        raise ToolExecutionError(
            "terminal.execute", "'timeout_s' must be a number"
        ) from None
    if not (timeout_s > 0) or timeout_s != timeout_s:
        raise ToolExecutionError(
            "terminal.execute", "'timeout_s' must be positive"
        )
    # The contract timeout is the ceiling: a caller cannot extend the
    # hold on a worker beyond what the registry advertises.
    timeout_s = min(timeout_s, CONTRACT_TIMEOUT_S)
    cwd = _resolve_cwd(arguments.get("cwd"), root)
    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            shell=False,
            stdin=subprocess.DEVNULL,
            env=_scrubbed_env(),
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except FileNotFoundError:
        raise ToolExecutionError(
            "terminal.execute", f"executable not found: {command[0]!r}"
        ) from None
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass  # best effort; the runner deadline still bounds the call
        raise ToolTimeoutError("terminal.execute", timeout_s) from None
    output = stdout + stderr
    truncated = len(output) > OUTPUT_CAP
    if truncated:
        output = output[:OUTPUT_CAP] + "\n[truncated: output exceeded cap]"
    return ToolResult(
        success=proc.returncode == 0,
        output=output,
        exit_code=proc.returncode,
        error=None if proc.returncode == 0 else f"exit {proc.returncode}",
    )


def terminal_tools(root: object = None) -> list[tuple[Tool, object]]:
    """Build the (contract, handler) pair for terminal.execute.

    ``root`` jails ``cwd`` (and becomes the default cwd); None keeps
    the historical unjailed behavior for tests and operator shells.
    """
    resolved = None
    if root is not None:
        from pathlib import Path

        resolved = str(Path(root).resolve())
    return [
        (
            Tool(
                name="terminal.execute",
                description="Run an argv command without a shell.",
                input_schema={
                    "required": ["command"],
                    "properties": {
                        "command": "array",
                        "timeout_s": "number",
                        "cwd": "string",
                    },
                },
                risk_level=RiskLevel.HIGH,
                timeout_s=120.0,
                capabilities=["subprocess"],
            ),
            _build_execute(resolved),
        ),
    ]
