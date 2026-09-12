"""Gate 6: risk classification, policy decisions, authorization choke point."""

import pytest

from ai_ecosystem.core.errors import AuthorizationDeniedError, DomainValidationError
from ai_ecosystem.core.models import Tool, ToolCall
from ai_ecosystem.core.models.enums import PermissionDecision, RiskLevel
from ai_ecosystem.security import (
    AuthorizationManager,
    Policy,
    PolicyEngine,
    RiskContext,
    RiskEngine,
)
from ai_ecosystem.tools import GrantAllAuthorizer, ToolRegistry, ToolRunner, filesystem_tools, terminal_tools


@pytest.fixture()
def registry(tmp_path):
    reg = ToolRegistry()
    for tool, handler in filesystem_tools(tmp_path):
        reg.register(tool, handler)
    for tool, handler in terminal_tools():
        reg.register(tool, handler)
    return reg


@pytest.fixture()
def manager(registry, tmp_path):
    return AuthorizationManager(
        registry, context=RiskContext(agent_id="test-agent", root=str(tmp_path))
    )


def test_read_file_is_low_and_allowed(manager, registry):
    call = registry.build_call("t", "filesystem.read", {"path": "a.txt"})
    permission = manager.authorize("t", registry.get("filesystem.read"), call)
    assert permission.decision is PermissionDecision.GRANTED
    assert permission.policy == "default"


def test_terminal_is_critical_and_denied_by_default(manager, registry):
    call = registry.build_call("t", "terminal.execute", {"command": ["echo", "hi"]})
    permission = manager.authorize("t", registry.get("terminal.execute"), call)
    assert permission.decision is PermissionDecision.DENIED
    assert "requires human approval" in permission.reason


def test_path_traversal_is_critical(manager, registry):
    engine = RiskEngine()
    call = registry.build_call("t", "filesystem.read", {"path": "../../etc/passwd"})
    assessment = engine.assess("t", registry.get("filesystem.read"), call, RiskContext(root="/tmp/root"))
    assert assessment.level is RiskLevel.CRITICAL
    assert any("traversal" in f for f in assessment.factors)


def test_critical_denied_even_under_permissive_policy(registry, tmp_path):
    permissive = AuthorizationManager(
        registry,
        policy_engine=PolicyEngine(Policy(name="lax", auto_grant_up_to=RiskLevel.HIGH)),
        context=RiskContext(root=str(tmp_path)),
    )
    call = registry.build_call("t", "filesystem.read", {"path": "../escape.txt"})
    permission = permissive.authorize("t", registry.get("filesystem.read"), call)
    assert permission.decision is PermissionDecision.DENIED


def test_command_injection_pattern_in_string_arg(registry):
    engine = RiskEngine()
    tool = Tool(name="raw.exec", input_schema={"required": ["cmd"]})
    call = ToolCall(task_id="t", tool="raw.exec", arguments={"cmd": "x; rm -rf /"})
    assessment = engine.assess("t", tool, call, RiskContext())
    assert assessment.level is RiskLevel.HIGH


def test_argv_metachars_stay_literal_but_noted(registry):
    engine = RiskEngine()
    call = registry.build_call("t", "terminal.execute", {"command": ["echo", "a; b"]})
    assessment = engine.assess("t", registry.get("terminal.execute"), call, RiskContext())
    assert assessment.level is RiskLevel.CRITICAL
    assert any("passed literally" in f for f in assessment.factors)


def test_malformed_call_rejected(manager, registry):
    call = registry.build_call("t", "filesystem.read", {})
    with pytest.raises(DomainValidationError):
        manager.authorize("t", registry.get("filesystem.read"), call)


def test_agent_scope_enforced(registry, tmp_path):
    scoped = AuthorizationManager(
        registry,
        policy_engine=PolicyEngine(Policy(name="scoped", agent_scopes={"reader": {"filesystem.read"}})),
        context=RiskContext(agent_id="reader", root=str(tmp_path)),
    )
    denied = scoped.authorize(
        "t",
        registry.get("terminal.execute"),
        registry.build_call("t", "terminal.execute", {"command": ["x"]}),
    )
    assert denied.decision is PermissionDecision.DENIED
    assert "scope" in denied.reason


def test_unauthorized_action_never_reaches_handler(registry, tmp_path):
    executed = []
    registry.register(Tool(name="danger", input_schema={"required": []}), lambda args: executed.append(True))
    strict = AuthorizationManager(
        registry, policy_engine=PolicyEngine(Policy(name="strict", denied_tools={"danger"}))
    )
    runner = ToolRunner(registry, strict)
    result = runner.run(registry.build_call("t", "danger", {}))
    assert result.success is False
    assert executed == []


def test_enforce_raises_on_denial(manager, registry):
    with pytest.raises(AuthorizationDeniedError):
        manager.enforce("t", registry.get("terminal.execute"), registry.build_call("t", "terminal.execute", {"command": ["x"]}))


def test_no_path_from_intention_to_tool_without_policy(registry, tmp_path):
    runner = ToolRunner(registry, AuthorizationManager(registry, context=RiskContext(root=str(tmp_path))))
    forged = ToolCall(task_id="t", tool="terminal.execute", arguments={"command": ["echo", "pwned"]})
    result = runner.run(forged)
    assert result.success is False
    assert "denied" in result.error


def test_shell_interpreters_blocked_by_default(registry):
    runner = ToolRunner(registry, AuthorizationManager(registry))
    for shell in (["cmd", "/c", "echo hi"], ["powershell", "-Command", "echo hi"], ["bash", "-c", "echo hi"]):
        result = runner.run(registry.build_call("t", "terminal.execute", {"command": shell}))
        assert result.success is False, shell
        assert "requires human approval" in result.error, shell


def test_shell_interpreters_allowed_with_opt_in(registry):
    manager = AuthorizationManager(
        registry,
        allow_shells=True,
        policy_engine=PolicyEngine(
            Policy(name="shells-ok", auto_grant_up_to=RiskLevel.CRITICAL, deny_critical=False)
        ),
    )
    permission = manager.authorize(
        "t",
        registry.get("terminal.execute"),
        registry.build_call("t", "terminal.execute", {"command": ["cmd", "/c", "echo hi"]}),
    )
    assert permission.decision is PermissionDecision.DENIED
    assert "requires human approval" in permission.reason
    assert "shell interpreter" not in permission.reason


def test_task_id_mismatch_denied(registry):
    manager = AuthorizationManager(registry)
    call = registry.build_call("task-a", "filesystem.read", {"path": "x"})
    denied = manager.authorize("task-b", registry.get("filesystem.read"), call)
    assert denied.decision is PermissionDecision.DENIED
    assert "mismatch" in denied.reason


def test_git_cwd_jailed_to_root(tmp_path):
    from ai_ecosystem.tools import ToolRegistry, ToolRunner, git_tools

    reg = ToolRegistry()
    for tool, handler in git_tools(tmp_path):
        reg.register(tool, handler)
    runner = ToolRunner(reg, GrantAllAuthorizer())
    outside = str(tmp_path.parent)
    escaped = runner.run(reg.build_call("t", "git.status", {"cwd": outside}))
    assert escaped.success is False
    assert "escapes" in escaped.error
