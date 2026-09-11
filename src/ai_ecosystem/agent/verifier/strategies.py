"""Deterministic verification strategies (Gate 9).

Strategies are pure checks over observed evidence -- ToolResults, files,
artifacts. No LLM, no network. A strategy returns a Finding; the
:class:`Verifier` aggregates findings into a structured result.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ai_ecosystem.core.errors.exceptions import ToolExecutionError
from ai_ecosystem.core.models.domain import ToolResult


@dataclass
class VerificationTarget:
    """Everything a strategy may inspect (never the executor itself)."""

    tool_results: list[ToolResult] = field(default_factory=list)
    root: str = ""
    task_id: str = ""


@dataclass
class Finding:
    """One strategy's verdict: passed / failed / cannot-tell."""

    passed: bool | None  # True / False / None (inconclusive)
    evidence: list[str] = field(default_factory=list)
    reason: str = ""


def safe_path(root: str, raw: str) -> Path:
    """Resolve ``raw`` under ``root``; raises ToolExecutionError on escape."""
    base = Path(root or ".").resolve()
    candidate = (base / raw).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise ToolExecutionError(
            "verifier", f"path {raw!r} escapes the allowed root"
        ) from None
    return candidate


class VerificationStrategy(ABC):
    """One deterministic check."""

    name: str = "base"

    @abstractmethod
    def check(self, target: VerificationTarget, params: dict[str, Any]) -> Finding:
        """Inspect the target; may raise (the Verifier maps it to ERROR)."""
        raise NotImplementedError


class CommandResultStrategy(VerificationStrategy):
    """Did execution itself succeed? Never sufficient alone for semantics."""

    name = "command_results"

    def check(self, target: VerificationTarget, params: dict[str, Any]) -> Finding:
        if not target.tool_results:
            return Finding(None, [], "no tool results to inspect")
        failures = [r for r in target.tool_results if not r.success]
        if failures:
            return Finding(
                False,
                [f"{r.tool_call_id}: {r.error or 'failed'}" for r in failures],
                f"{len(failures)}/{len(target.tool_results)} tool calls failed",
            )
        expected = params.get("expect_in_output")
        if expected:
            missing = [
                r.tool_call_id
                for r in target.tool_results
                if expected not in str(r.output or "")
            ]
            if missing:
                return Finding(
                    False,
                    [f"{cid}: output lacks {expected!r}" for cid in missing],
                    "output does not satisfy completion criteria",
                )
        return Finding(
            True,
            [f"{len(target.tool_results)} tool calls succeeded"],
            "all observed tool calls succeeded",
        )


class ArtifactExistsStrategy(VerificationStrategy):
    """Do the expected files/artifacts actually exist? (false-success killer)."""

    name = "artifact_exists"

    def check(self, target: VerificationTarget, params: dict[str, Any]) -> Finding:
        paths = params.get("paths", [])
        if not paths:
            return Finding(None, [], "no expected paths specified")
        missing = [p for p in paths if not safe_path(target.root, p).exists()]
        if missing:
            return Finding(
                False,
                [f"missing: {p}" for p in missing],
                "expected artifact does not exist despite successful execution",
            )
        return Finding(
            True, [f"exists: {p}" for p in paths], "all expected artifacts exist"
        )


class ArtifactPropertyStrategy(VerificationStrategy):
    """Do artifacts satisfy basic properties (size, text content)?"""

    name = "artifact_properties"

    def check(self, target: VerificationTarget, params: dict[str, Any]) -> Finding:
        path = params.get("path", "")
        if not path:
            return Finding(None, [], "no path specified")
        target_path = safe_path(target.root, path)
        if not target_path.is_file():
            return Finding(False, [f"not a file: {path}"], "expected file is absent")
        evidence = []
        min_bytes = int(params.get("min_bytes", 1))
        size = target_path.stat().st_size
        if size < min_bytes:
            return Finding(
                False,
                [f"{path}: {size} bytes < {min_bytes} minimum"],
                "artifact is missing or corrupt",
            )
        evidence.append(f"{path}: {size} bytes")
        expected_text = params.get("contains")
        if expected_text:
            try:
                content = target_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                return Finding(
                    False, [f"{path}: unreadable ({exc})"], "artifact is corrupt"
                )
            if expected_text not in content:
                return Finding(
                    False,
                    [f"{path}: lacks {expected_text!r}"],
                    "artifact does not satisfy completion criteria",
                )
            evidence.append(f"{path}: contains {expected_text!r}")
        return Finding(True, evidence, "artifact properties satisfied")


class TestCommandStrategy(VerificationStrategy):
    """Run an explicit test command -- through ToolRunner, never directly."""

    name = "test_command"

    def __init__(self, runner: Any, registry: Any) -> None:
        self._runner = runner
        self._registry = registry

    def check(self, target: VerificationTarget, params: dict[str, Any]) -> Finding:
        command = params.get("command")
        if not command:
            return Finding(None, [], "no test command specified")
        call = self._registry.build_call(
            target.task_id, "terminal.execute", {"command": command}
        )
        result = self._runner.run(call)
        if (result.error or "").lower().startswith("denied:"):
            return Finding(
                None,
                ["test command was denied by policy"],
                "cannot determine test outcome; command not authorized",
            )
        if result.success:
            return Finding(
                True,
                [f"test output: {str(result.output)[:500]}"],
                "test command passed",
            )
        return Finding(
            False,
            [f"test failed: {result.error or result.output}"],
            "test command reported failure",
        )
