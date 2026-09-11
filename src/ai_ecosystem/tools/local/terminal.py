"""Terminal execution tool with explicit dangerous-capability metadata."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ai_ecosystem.core.errors.exceptions import ToolExecutionError, ToolTimeoutError
from ai_ecosystem.core.models.domain import Tool, ToolResult
from ai_ecosystem.core.models.enums import RiskLevel
from ai_ecosystem.core.secrets import redact
from ai_ecosystem.tools.registry.registry import ToolHandler
import contextlib

OUTPUT_CAP = 100_000
CONTRACT_TIMEOUT_S = 120.0


def _scrubbed_env(allowlist: frozenset[str] | None = None) -> dict[str, str]:
    if allowlist is not None:
        return {key: os.environ[key] for key in allowlist if key in os.environ}
    redacted = redact(dict(os.environ))
    return {key: value for key, value in redacted.items() if value != "***"}


def _resolve_cwd(raw: object, root: object) -> str | None:
    if raw is None or raw == "":
        return str(root) if root is not None else None
    if not isinstance(raw, str):
        raise ToolExecutionError("terminal.execute", "'cwd' must be an existing directory")
    candidate = Path(raw)
    if root is not None:
        resolved = (Path(root) / candidate).resolve()
        try:
            resolved.relative_to(Path(root).resolve())
        except ValueError:
            raise ToolExecutionError("terminal.execute", "'cwd' escapes the allowed root") from None
        if not resolved.is_dir():
            raise ToolExecutionError("terminal.execute", "'cwd' must be an existing directory")
        return str(resolved)
    if not candidate.is_dir():
        raise ToolExecutionError("terminal.execute", "'cwd' must be an existing directory")
    return str(candidate.resolve())


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5, check=False)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    with contextlib.suppress(OSError):
        proc.kill()


def _run(arguments: dict, root: object,
         env_allowlist: frozenset[str] | None = None) -> ToolResult:
    command = arguments.get("command")
    if not isinstance(command, list) or not command or any(not isinstance(part, str) for part in command):
        raise ToolExecutionError("terminal.execute", "'command' must be a non-empty argv list of strings")
    try:
        timeout_s = float(arguments.get("timeout_s", 60.0))
    except (TypeError, ValueError):
        raise ToolExecutionError("terminal.execute", "'timeout_s' must be a number") from None
    if not (timeout_s > 0) or timeout_s != timeout_s:
        raise ToolExecutionError("terminal.execute", "'timeout_s' must be positive")
    timeout_s = min(timeout_s, CONTRACT_TIMEOUT_S)
    cwd = _resolve_cwd(arguments.get("cwd"), root)
    kwargs = dict(args=command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                  text=True, cwd=cwd, shell=False, stdin=subprocess.DEVNULL,
                  env=_scrubbed_env(env_allowlist))
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(**kwargs)
    except FileNotFoundError:
        raise ToolExecutionError("terminal.execute", f"executable not found: {command[0]!r}") from None
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        raise ToolTimeoutError("terminal.execute", timeout_s) from None
    output = stdout + stderr
    if len(output) > OUTPUT_CAP:
        output = output[:OUTPUT_CAP] + "\n[truncated: output exceeded cap]"
    return ToolResult(success=proc.returncode == 0, output=output,
                      exit_code=proc.returncode,
                      error=None if proc.returncode == 0 else f"exit {proc.returncode}",
                      rollback={"rollbackable": False,
                                "reason": "arbitrary process side effects cannot be undone"})


def terminal_tools(root: object = None,
                   env_allowlist: frozenset[str] | None = None) -> list[tuple[Tool, ToolHandler]]:
    resolved = str(Path(root).resolve()) if root is not None else None
    return [(Tool(
        name="terminal.execute",
        description="Run a direct argv process through the sandbox supervisor; explicit human authorization required.",
        input_schema={"required": ["command"],
                      "properties": {"command": "array", "timeout_s": "number", "cwd": "string"}},
        risk_level=RiskLevel.CRITICAL,
        timeout_s=CONTRACT_TIMEOUT_S,
        capabilities=["subprocess", "arbitrary-code-execution"],
        requires_approval=True,
        requires_sandbox=True,
        sandbox_profile="terminal",
        network_access=True,
    ), lambda arguments, workspace=resolved, allowlist=env_allowlist: _run(arguments, workspace, allowlist))]
