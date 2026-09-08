"""Git inspection tools (read-only; fixed argv, never a shell)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ai_ecosystem.core.errors.exceptions import ToolExecutionError
from ai_ecosystem.core.models.domain import Tool, ToolResult
from ai_ecosystem.core.models.enums import RiskLevel

OUTPUT_CAP = 100_000
#: Contract timeout mirror (see terminal.CONTRACT_TIMEOUT_S).
CONTRACT_TIMEOUT_S = 30.0


def _resolve_cwd(raw: object, root: object, tool: str) -> str:
    """Default empty cwd to root; jail resolved cwd under root when set."""
    from pathlib import Path

    if not raw:
        if root is None:
            raise ToolExecutionError(tool, "missing 'cwd' argument")
        return str(root)
    if not isinstance(raw, str):
        raise ToolExecutionError(tool, "'cwd' must be a directory path")
    candidate = Path(raw)
    if root is not None:
        # Relative paths resolve against the root (like filesystem tools),
        # then the result must stay inside it.
        resolved = (Path(root) / candidate).resolve()
        try:
            resolved.relative_to(Path(root).resolve())
        except ValueError:
            raise ToolExecutionError(
                tool, "'cwd' escapes the allowed root") from None
        raw = str(resolved)
    if not Path(raw).is_dir():
        raise ToolExecutionError(tool, f"not a directory: {raw!r}")
    return raw


def _run_git(cwd: str, args: list[str], timeout_s: float) -> str:
    try:
        proc = subprocess.Popen(
            ["git", "-C", cwd, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            stdin=subprocess.DEVNULL,
            shell=False,
        )
    except FileNotFoundError:
        raise ToolExecutionError("git", "git executable not found") from None
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        raise ToolExecutionError("git", "git command timed out") from None
    if proc.returncode != 0:
        raise ToolExecutionError("git", stderr.strip() or "git failed")
    truncated = len(stdout) > OUTPUT_CAP
    if truncated:
        stdout = stdout[:OUTPUT_CAP] + "\n[truncated: output exceeded cap]"
    return stdout


def _status(arguments: dict, root: object = None) -> ToolResult:
    cwd = _resolve_cwd(arguments.get("cwd", ""), root, "git.status")
    timeout_s = _timeout(arguments)
    out = _run_git(cwd, ["status", "--short", "--branch"], timeout_s)
    return ToolResult(success=True, output=out)


def _diff(arguments: dict, root: object = None) -> ToolResult:
    cwd = _resolve_cwd(arguments.get("cwd", ""), root, "git.diff")
    timeout_s = _timeout(arguments)
    out = _run_git(cwd, ["diff", "--stat", "--", "."], timeout_s)
    return ToolResult(success=True, output=out)


def _timeout(arguments: dict) -> float:
    """Caller timeout clamped to the contract (never extended)."""
    try:
        timeout_s = float(arguments.get("timeout_s", CONTRACT_TIMEOUT_S))
    except (TypeError, ValueError):
        raise ToolExecutionError("git", "'timeout_s' must be a number") from None
    if not (timeout_s > 0) or timeout_s != timeout_s:
        raise ToolExecutionError("git", "'timeout_s' must be positive")
    return min(timeout_s, CONTRACT_TIMEOUT_S)


def _diff(arguments: dict, root: object = None) -> ToolResult:
    cwd = arguments.get("cwd", "") or (str(root) if root is not None else "")
    if not cwd:
        raise ToolExecutionError("git.diff", "missing 'cwd' argument")
    out = _run_git(cwd, ["diff", "--stat", "--", "."], 30.0)
    return ToolResult(success=True, output=out)


def git_tools(root: object = None) -> list[tuple[Tool, object]]:
    """Build (contract, handler) pairs for git.status / git.diff.

    ``root`` becomes the default ``cwd`` (still validated as a
    directory per call); None keeps the historical behavior of
    requiring an explicit cwd.
    """
    from functools import partial

    return [
        (
            Tool(
                name="git.status",
                description="Show short git status for a repository.",
                input_schema={"required": ["cwd"],
                              "properties": {"cwd": "string",
                                             "timeout_s": "number"}},
                risk_level=RiskLevel.LOW,
                timeout_s=30.0,
                capabilities=["read-only"],
            ),
            partial(_status, root=root),
        ),
        (
            Tool(
                name="git.diff",
                description="Show git diff stat for a repository.",
                input_schema={"required": ["cwd"],
                              "properties": {"cwd": "string",
                                             "timeout_s": "number"}},
                risk_level=RiskLevel.LOW,
                timeout_s=30.0,
                capabilities=["read-only"],
            ),
            partial(_diff, root=root),
        ),
    ]
