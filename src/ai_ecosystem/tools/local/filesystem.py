"""Filesystem tools, confined to a root directory (defense in depth).

Even though the policy layer (Gate 6) also rejects escapes, the tools
themselves refuse to touch anything outside ``root`` so a policy bug
alone cannot cause an escape.
"""

from __future__ import annotations

from pathlib import Path

from ai_ecosystem.core.errors.exceptions import ToolExecutionError
from ai_ecosystem.core.models.domain import Tool, ToolResult
from ai_ecosystem.core.models.enums import RiskLevel
from ai_ecosystem.tools.registry.registry import ToolHandler

MAX_READ_BYTES = 1_000_000
MAX_LIST_ENTRIES = 5_000


def _resolve(root: Path, raw: str) -> Path:
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        raise ToolExecutionError(
            "filesystem", f"path {raw!r} escapes the allowed root"
        ) from None
    return candidate


def _read(arguments: dict, root: Path) -> ToolResult:
    raw = arguments.get("path", "")
    if not raw:
        raise ToolExecutionError("filesystem.read", "missing 'path' argument")
    target = _resolve(root, raw)
    if not target.is_file():
        raise ToolExecutionError("filesystem.read", f"not a file: {raw!r}")
    try:
        if target.stat().st_size > MAX_READ_BYTES:
            raise ToolExecutionError(
                "filesystem.read",
                f"file too large ({target.stat().st_size} bytes)")
    except OSError as exc:
        raise ToolExecutionError(
            "filesystem.read", f"cannot stat {raw!r}") from exc
    data = target.read_bytes()
    if len(data) > MAX_READ_BYTES:  # raced growth between stat and read
        raise ToolExecutionError(
            "filesystem.read", f"file too large ({len(data)} bytes)"
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ToolExecutionError(
            "filesystem.read", f"not decodable as UTF-8: {raw!r}"
        ) from None
    return ToolResult(success=True, output=text)


def _list(arguments: dict, root: Path) -> ToolResult:
    raw = arguments.get("path", ".")
    target = _resolve(root, raw)
    if not target.is_dir():
        raise ToolExecutionError("filesystem.list", f"not a directory: {raw!r}")
    names = sorted(p.name for p in target.iterdir())
    if len(names) > MAX_LIST_ENTRIES:
        names = names[:MAX_LIST_ENTRIES]
        names.append(f"[truncated: more than {MAX_LIST_ENTRIES} entries]")
    return ToolResult(success=True, output=names)


def _exists(arguments: dict, root: Path) -> ToolResult:
    raw = arguments.get("path", "")
    if not raw:
        raise ToolExecutionError("filesystem.exists", "missing 'path' argument")
    return ToolResult(success=True, output=_resolve(root, raw).exists())


def filesystem_tools(root: str | Path) -> list[tuple[Tool, ToolHandler]]:
    """Build (contract, handler) pairs for filesystem.read/list/exists."""
    base = Path(root)
    return [
        (
            Tool(
                name="filesystem.read",
                description="Read a UTF-8 text file under the allowed root.",
                input_schema={"required": ["path"],
                              "properties": {"path": "string"}},
                risk_level=RiskLevel.LOW,
                capabilities=["read-only"],
            ),
            lambda args: _read(args, base),
        ),
        (
            Tool(
                name="filesystem.list",
                description="List directory entries under the allowed root.",
                input_schema={"required": [],
                              "properties": {"path": "string"}},
                risk_level=RiskLevel.LOW,
                capabilities=["read-only"],
            ),
            lambda args: _list(args, base),
        ),
        (
            Tool(
                name="filesystem.exists",
                description="Check whether a path exists under the allowed root.",
                input_schema={"required": ["path"],
                              "properties": {"path": "string"}},
                risk_level=RiskLevel.LOW,
                capabilities=["read-only"],
            ),
            lambda args: _exists(args, base),
        ),
    ]
