"""Gate 5: registry, runner lifecycle, initial tools, MCP bridge."""

import subprocess
import sys

import pytest

from ai_ecosystem.core.errors import DomainValidationError, ToolExecutionError
from ai_ecosystem.core.events import Event, EventBus
from ai_ecosystem.core.models import Tool, ToolCall, ToolResult
from ai_ecosystem.core.models.enums import EventType, RiskLevel
from ai_ecosystem.tools import (
    DenyAllAuthorizer,
    GrantAllAuthorizer,
    ToolRegistry,
    ToolRunner,
    adapt_tool,
    connect_server,
    filesystem_tools,
    git_tools,
    is_available,
    terminal_tools,
)


@pytest.fixture()
def registry(tmp_path):
    reg = ToolRegistry()
    for tool, handler in filesystem_tools(tmp_path):
        reg.register(tool, handler)
    for tool, handler in git_tools():
        reg.register(tool, handler)
    for tool, handler in terminal_tools():
        reg.register(tool, handler)
    return reg


def test_registry_lists_initial_toolset(registry):
    names = [t.name for t in registry.list_tools()]
    assert names == [
        "filesystem.exists",
        "filesystem.list",
        "filesystem.read",
        "git.diff",
        "git.status",
        "terminal.execute",
    ]


def test_registry_rejects_duplicates(registry):
    with pytest.raises(DomainValidationError):
        registry.register(Tool(name="filesystem.read"), lambda args: None)


def test_registry_rejects_unknown_build(registry):
    with pytest.raises(DomainValidationError):
        registry.build_call("t", "nope", {})


def test_filesystem_read_list_exists(registry, tmp_path):
    runner = ToolRunner(registry, GrantAllAuthorizer())
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "sub").mkdir()
    read = runner.run(registry.build_call("t", "filesystem.read", {"path": "a.txt"}))
    assert read.success and read.output == "hello"
    listed = runner.run(registry.build_call("t", "filesystem.list", {"path": "."}))
    assert set(listed.output) == {"a.txt", "sub"}
    exists = runner.run(
        registry.build_call("t", "filesystem.exists", {"path": "a.txt"})
    )
    assert exists.success and exists.output is True


def test_filesystem_escape_refused(registry):
    runner = ToolRunner(registry, GrantAllAuthorizer())
    result = runner.run(
        registry.build_call("t", "filesystem.read", {"path": "../secret.txt"})
    )
    assert result.success is False
    assert "escapes" in result.error


def test_filesystem_missing_file(registry):
    runner = ToolRunner(registry, GrantAllAuthorizer())
    result = runner.run(
        registry.build_call("t", "filesystem.read", {"path": "nope.txt"})
    )
    assert result.success is False


def _git_repo(path):
    for args in (
        ["init"],
        ["config", "user.email", "t@t.t"],
        ["config", "user.name", "t"],
        ["commit", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


def test_git_status_and_diff(registry, tmp_path):
    _git_repo(tmp_path)
    (tmp_path / "f.txt").write_text("x")
    runner = ToolRunner(registry, GrantAllAuthorizer())
    status = runner.run(
        registry.build_call("t", "git.status", {"cwd": str(tmp_path)})
    )
    assert status.success and "f.txt" in status.output
    diff = runner.run(registry.build_call("t", "git.diff", {"cwd": str(tmp_path)}))
    assert diff.success


def test_git_outside_repo_fails(registry, tmp_path):
    runner = ToolRunner(registry, GrantAllAuthorizer())
    result = runner.run(
        registry.build_call("t", "git.status", {"cwd": str(tmp_path)})
    )
    assert result.success is False


def test_terminal_executes_argv(registry):
    runner = ToolRunner(registry, GrantAllAuthorizer())
    result = runner.run(
        registry.build_call(
            "t",
            "terminal.execute",
            {"command": [sys.executable, "-c", "print('hi')"]},
        )
    )
    assert result.success and "hi" in result.output
    assert result.exit_code == 0


def test_terminal_rejects_string_command(registry):
    runner = ToolRunner(registry, GrantAllAuthorizer())
    result = runner.run(
        registry.build_call("t", "terminal.execute", {"command": "rm -rf /"})
    )
    assert result.success is False
    assert "argv list" in result.error


def test_terminal_shell_metachars_are_literal(registry):
    runner = ToolRunner(registry, GrantAllAuthorizer())
    result = runner.run(
        registry.build_call(
            "t",
            "terminal.execute",
            {"command": [sys.executable, "-c", "print('a; rm -rf /')"]},
        )
    )
    assert result.success and "a; rm -rf /" in result.output


def test_terminal_timeout(registry):
    runner = ToolRunner(registry, GrantAllAuthorizer())
    tool = registry.get("terminal.execute")
    tool.timeout_s = 0.2
    call = registry.build_call(
        "t",
        "terminal.execute",
        {"command": [sys.executable, "-c", "import time; time.sleep(5)"]},
    )
    result = runner.run(call)
    assert result.success is False
    assert "timed out" in result.error


def test_terminal_cwd_jailed_to_root(tmp_path):
    from ai_ecosystem.tools.local.terminal import terminal_tools as rooted

    reg = ToolRegistry()
    for tool, handler in rooted(tmp_path):
        reg.register(tool, handler)
    runner = ToolRunner(reg, GrantAllAuthorizer())
    outside = str(tmp_path.parent)

    def run(cwd):
        return runner.run(reg.build_call(
            "t", "terminal.execute",
            {"command": [sys.executable, "-c", "print('hi')"], "cwd": cwd}))

    assert run(str(tmp_path)).success is True
    escaped = run(outside)
    assert escaped.success is False and "escapes" in escaped.error


def test_terminal_env_scrubs_credentials(tmp_path, monkeypatch):
    from ai_ecosystem.tools.local.terminal import terminal_tools as rooted

    monkeypatch.setenv("AI_ECO_MODEL_API_KEY", "sk-test-0123456789abcdef")
    monkeypatch.setenv("TOTALLY_INNOCENT_VAR", "visible")
    reg = ToolRegistry()
    for tool, handler in rooted(tmp_path):
        reg.register(tool, handler)
    runner = ToolRunner(reg, GrantAllAuthorizer())
    result = runner.run(reg.build_call(
        "t", "terminal.execute",
        {"command": [sys.executable, "-c",
                     "import os; print('KEY:' + os.environ.get("
                     "'AI_ECO_MODEL_API_KEY', 'ABSENT')); print('VAR:' + "
                     "os.environ.get('TOTALLY_INNOCENT_VAR', 'ABSENT'))"]}))
    assert result.success is True
    assert "sk-test-0123456789abcdef" not in result.output
    assert "VAR:visible" in result.output


def test_terminal_truncation_marked():
    from ai_ecosystem.tools.local import terminal

    old_cap, terminal.OUTPUT_CAP = terminal.OUTPUT_CAP, 16
    try:
        reg = ToolRegistry()
        for tool, handler in terminal.terminal_tools():
            reg.register(tool, handler)
        runner = ToolRunner(reg, GrantAllAuthorizer())
        result = runner.run(reg.build_call(
            "t", "terminal.execute",
            {"command": [sys.executable, "-c", "print('x' * 100)"]}))
    finally:
        terminal.OUTPUT_CAP = old_cap
    assert result.success is True
    assert "[truncated" in result.output


def test_runner_missing_argument(registry):
    runner = ToolRunner(registry, GrantAllAuthorizer())
    result = runner.run(registry.build_call("t", "filesystem.read", {}))
    assert result.success is False
    assert "missing required" in result.error


def test_runner_denied_never_executes(registry):
    executed = []
    registry.register(
        Tool(name="evil", input_schema={"required": []}),
        lambda args: executed.append(True) or ToolResult(success=True),
    )
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe_all(seen.append)
    runner = ToolRunner(registry, DenyAllAuthorizer(), bus)
    result = runner.run(registry.build_call("t", "evil", {}))
    assert result.success is False
    assert executed == []
    kinds = [e.event_type for e in seen]
    assert EventType.PERMISSION_DENIED in kinds
    assert EventType.TOOL_STARTED not in kinds


def test_runner_emits_lifecycle_events(registry):
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe_all(seen.append)
    runner = ToolRunner(registry, GrantAllAuthorizer(), bus)
    runner.run(registry.build_call("t1", "filesystem.list", {"path": "."}))
    kinds = [e.event_type for e in seen]
    assert kinds == [
        EventType.TOOL_REQUESTED,
        EventType.TOOL_STARTED,
        EventType.TOOL_COMPLETED,
    ]
    assert all(e.task_id == "t1" for e in seen)


def test_runner_isolates_crashing_handler():
    registry = ToolRegistry()
    registry.register(Tool(name="boom", input_schema={"required": []}), _crash)
    runner = ToolRunner(registry, GrantAllAuthorizer())
    result = runner.run(registry.build_call("t", "boom", {}))
    assert result.success is False
    assert "crashed" in result.error


def _crash(args):
    raise RuntimeError("handler bug")


def test_mcp_bridge_adapts_plain_function():
    tool, handler = adapt_tool(
        "upper", "Uppercase text", {"required": ["text"]}, lambda a: a["text"].upper()
    )
    registry = ToolRegistry()
    registry.register(tool, handler)
    runner = ToolRunner(registry, GrantAllAuthorizer())
    result = runner.run(registry.build_call("t", "upper", {"text": "hi"}))
    assert result.success and result.output == "HI"


def test_mcp_bridge_reports_missing_package():
    if is_available():
        pytest.skip("fastmcp installed; live path not tested here")
    with pytest.raises(ToolExecutionError, match="fastmcp is not installed"):
        connect_server("http://localhost:9999")


def test_every_tool_execution_is_identified(registry):
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe(EventType.TOOL_COMPLETED, seen.append)
    runner = ToolRunner(registry, GrantAllAuthorizer(), bus)
    call = registry.build_call("task-42", "filesystem.list", {"path": "."})
    runner.run(call)
    assert seen[0].payload["call_id"] == call.id
    assert seen[0].task_id == "task-42"


def test_respond_returns_text_unchanged():
    from ai_ecosystem.tools import respond_tools

    registry = ToolRegistry()
    for tool, handler in respond_tools():
        registry.register(tool, handler)
    assert registry.get("agent.respond").risk_level is RiskLevel.LOW
    runner = ToolRunner(registry, GrantAllAuthorizer())
    result = runner.run(
        registry.build_call("t", "agent.respond", {"text": "Hello there!"}))
    assert result.success is True
    assert result.output == "Hello there!"


def test_respond_rejects_missing_or_huge_text():
    from ai_ecosystem.tools import respond_tools

    registry = ToolRegistry()
    for tool, handler in respond_tools():
        registry.register(tool, handler)
    runner = ToolRunner(registry, GrantAllAuthorizer())
    assert runner.run(
        registry.build_call("t", "agent.respond", {})).success is False
    assert runner.run(
        registry.build_call(
            "t", "agent.respond", {"text": "x" * 9000})).success is False
