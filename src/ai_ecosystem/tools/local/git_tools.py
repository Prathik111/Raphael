"""Git inspection tools (read-only; fixed argv, never a shell)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ai_ecosystem.core.errors.exceptions import ToolExecutionError
from ai_ecosystem.core.models.domain import Tool, ToolResult
from ai_ecosystem.core.models.enums import RiskLevel

OUTPUT_CAP = 100_000


def _run_git(cwd: str, args: list[str], timeout_s: float) -> str:
    if not Path(cwd).is_dir():
        raise ToolExecutionError("git", f"not a directory: {cwd!r}")
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            shell=False,
        )
    except FileNotFoundError:
        raise ToolExecutionError("git", "git executable not found") from None
    except subprocess.TimeoutExpired:
        raise ToolExecutionError("git", "git command timed out") from None
    if proc.returncode != 0:
        raise ToolExecutionError("git", proc.stderr.strip() or "git failed")
    return proc.stdout[:OUTPUT_CAP]


def _status(arguments: dict, root: object = None) -> ToolResult:
    cwd = arguments.get("cwd", "") or (str(root) if root is not None else "")
    if not cwd:
        raise ToolExecutionError("git.status", "missing 'cwd' argument")
    out = _run_git(cwd, ["status", "--short", "--branch"], 30.0)
    return ToolResult(success=True, output=out)


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
                              "properties": {"cwd": "string"}},
                risk_level=RiskLevel.LOW,
                capabilities=["read-only"],
            ),
            partial(_status, root=root),
        ),
        (
            Tool(
                name="git.diff",
                description="Show git diff stat for a repository.",
                input_schema={"required": ["cwd"],
                              "properties": {"cwd": "string"}},
                risk_level=RiskLevel.LOW,
                capabilities=["read-only"],
            ),
            partial(_diff, root=root),
        ),
    ]
