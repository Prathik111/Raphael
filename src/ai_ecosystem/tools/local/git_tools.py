"""Git inspection tools (read-only; fixed argv, never a shell)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

from ai_ecosystem.core.errors.exceptions import ToolExecutionError
from ai_ecosystem.core.models.domain import Tool, ToolResult
from ai_ecosystem.core.models.enums import RiskLevel
from ai_ecosystem.tools.registry.registry import ToolHandler

OUTPUT_CAP: Final = 100_000
CONTRACT_TIMEOUT_S: Final = 30.0


def _resolve_cwd(raw: object, root: object, tool: str) -> str:
    """Resolve and strictly jail cwd beneath the configured workspace root."""
    if raw in (None, ""):
        if root is None:
            raise ToolExecutionError(tool, "missing 'cwd' argument")
        raw = str(root)
    if not isinstance(raw, str):
        raise ToolExecutionError(tool, "'cwd' must be a directory path")

    candidate = Path(raw)
    if root is not None:
        root_path = Path(root).resolve()
        resolved = (root_path / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        try:
            resolved.relative_to(root_path)
        except ValueError:
            raise ToolExecutionError(tool, "'cwd' escapes the allowed root") from None
    else:
        resolved = candidate.resolve()

    if not resolved.is_dir():
        raise ToolExecutionError(tool, f"not a directory: {resolved!r}")
    return str(resolved)


def _timeout(arguments: dict) -> float:
    try:
        timeout_s = float(arguments.get("timeout_s", CONTRACT_TIMEOUT_S))
    except (TypeError, ValueError):
        raise ToolExecutionError("git", "'timeout_s' must be a number") from None
    if not (timeout_s > 0) or timeout_s != timeout_s:
        raise ToolExecutionError("git", "'timeout_s' must be positive")
    return min(timeout_s, CONTRACT_TIMEOUT_S)


def _run_git(cwd: str, args: list[str], timeout_s: float) -> str:
    try:
        proc = subprocess.Popen(
            ["git", "-C", cwd, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            stdin=subprocess.DEVNULL,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except FileNotFoundError:
        raise ToolExecutionError("git", "git executable not found") from None

    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise ToolExecutionError("git", "git command timed out") from None

    if proc.returncode != 0:
        raise ToolExecutionError("git", stderr.strip() or "git failed")
    if len(stdout) > OUTPUT_CAP:
        stdout = stdout[:OUTPUT_CAP] + "\n[truncated: output exceeded cap]"
    return stdout


def _status(arguments: dict, root: object = None) -> ToolResult:
    cwd = _resolve_cwd(arguments.get("cwd"), root, "git.status")
    out = _run_git(cwd, ["status", "--short", "--branch"], _timeout(arguments))
    return ToolResult(success=True, output=out)


def _diff(arguments: dict, root: object = None) -> ToolResult:
    cwd = _resolve_cwd(arguments.get("cwd"), root, "git.diff")
    out = _run_git(cwd, ["diff", "--stat", "--", "."], _timeout(arguments))
    return ToolResult(success=True, output=out)


def git_tools(root: object = None) -> list[tuple[Tool, ToolHandler]]:
    """Build git.status / git.diff handlers with the same root jail."""
    from functools import partial

    return [
        (
            Tool(
                name="git.status",
                description="Show short git status for a repository.",
                input_schema={"required": ["cwd"], "properties": {"cwd": "string", "timeout_s": "number"}},
                risk_level=RiskLevel.LOW,
                timeout_s=CONTRACT_TIMEOUT_S,
                capabilities=["read-only"],
            ),
            partial(_status, root=root),
        ),
        (
            Tool(
                name="git.diff",
                description="Show git diff stat for a repository.",
                input_schema={"required": ["cwd"], "properties": {"cwd": "string", "timeout_s": "number"}},
                risk_level=RiskLevel.LOW,
                timeout_s=CONTRACT_TIMEOUT_S,
                capabilities=["read-only"],
            ),
            partial(_diff, root=root),
        ),
    ]
