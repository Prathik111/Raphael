"""Gates 29-31: trust tiers, sandbox isolation, tamper-evident audit."""

import os
import time

import pytest

from ai_ecosystem.core.errors import DomainValidationError, ToolExecutionError
from ai_ecosystem.core.models import Tool, ToolResult
from ai_ecosystem.core.models.enums import RiskLevel
from ai_ecosystem.core.persistence import Database
from ai_ecosystem.security import (
    AuditLog,
    AuthorizationManager,
    ComponentTrust,
    LocalSandboxProvider,
    SandboxProfile,
    TrustLevel,
    capability_risk,
    is_dangerous,
    meets,
)
from ai_ecosystem.tools import GrantAllAuthorizer, ToolRegistry, ToolRunner


def _registry():
    registry = ToolRegistry()
    registry.register(
        Tool(name="ok", input_schema={"required": []}, risk_level=RiskLevel.LOW),
        lambda args: ToolResult(success=True, output="ok"),
    )
    return registry


def test_trust_levels_ordered():
    assert meets(TrustLevel.SYSTEM, TrustLevel.UNTRUSTED)
    assert meets(TrustLevel.TRUSTED, TrustLevel.LIMITED)
    assert not meets(TrustLevel.LIMITED, TrustLevel.TRUSTED)
    assert meets(TrustLevel.UNTRUSTED, TrustLevel.UNTRUSTED) is not False


def test_no_self_elevation():
    trust = ComponentTrust()
    assert trust.level_of("plugin-x") is TrustLevel.UNTRUSTED
    with pytest.raises(DomainValidationError):
        trust.assign(
            "plugin-x", TrustLevel.TRUSTED, actor="plugin-x", actor_trust=TrustLevel.LIMITED
        )
    trust.assign("plugin-x", TrustLevel.LIMITED, actor="operator", actor_trust=TrustLevel.SYSTEM)
    assert trust.level_of("plugin-x") is TrustLevel.LIMITED
    with pytest.raises(DomainValidationError):
        trust.require("plugin-x", TrustLevel.TRUSTED)


def test_capability_danger_model():
    assert capability_risk("terminal.execute") == "HIGH"
    assert capability_risk("policy.modify") == "CRITICAL"
    assert capability_risk("filesystem.read") == "LOW"
    assert capability_risk("something.unknown") == "UNKNOWN"
    assert is_dangerous("terminal.execute") is True
    assert is_dangerous("filesystem.read") is False


def test_sandbox_timeout():
    provider = LocalSandboxProvider()

    def slow(args):
        time.sleep(5)
        return ToolResult(success=True, output="late")

    tool = Tool(name="slow", input_schema={"required": []})
    with pytest.raises(Exception, match="timed out"):
        provider.run(tool, slow, {}, SandboxProfile(name="t", timeout_s=0.2), 30.0)


def test_sandbox_filesystem_restriction(tmp_path):
    provider = LocalSandboxProvider()

    def probe(args):
        import os as _os

        cwd = args.get("cwd", "")
        return ToolResult(
            success=True,
            output=f"cwd={cwd}\nexists={_os.path.isdir(cwd)}",
        )

    tool = Tool(name="walk", input_schema={"required": []}, capabilities=["subprocess"])
    result = provider.run(
        tool, probe, {}, SandboxProfile(name="t", fs_root=str(tmp_path)), 5.0
    )
    assert result.success
    assert f"cwd={tmp_path}" in result.output
    assert "exists=True" in result.output


def test_sandbox_network_restriction():
    provider = LocalSandboxProvider()
    tool = Tool(
        name="fetch",
        input_schema={"required": []},
        capabilities=["network"],
        network_access=True,
    )
    with pytest.raises(Exception, match="network use denied"):
        provider.run(
            tool,
            lambda args: ToolResult(success=True),
            {},
            SandboxProfile(name="t", allow_network=False),
            5.0,
        )
    result = provider.run(
        tool,
        lambda args: ToolResult(success=True, output="net"),
        {},
        SandboxProfile(name="t", allow_network=True),
        5.0,
    )
    assert result.success


def test_sandbox_env_scrub():
    os.environ["AI_ECO_TEST_SECRET_KEY"] = "supersecret"
    provider = LocalSandboxProvider()

    def probe(args):
        import os as _os

        value = _os.environ.get("AI_ECO_TEST_SECRET_KEY")
        return ToolResult(success=True, output=f"secret={value!r}")

    try:
        result = provider.run(
            Tool(name="probe", input_schema={"required": []}),
            probe,
            {},
            SandboxProfile(name="t"),
            5.0,
        )
    finally:
        os.environ.pop("AI_ECO_TEST_SECRET_KEY", None)
    assert result.success
    assert "secret=None" in result.output
    assert "AI_ECO_TEST_SECRET_KEY" not in os.environ


def test_sandbox_cleanup_on_crash():
    provider = LocalSandboxProvider()

    def boom(args):
        raise RuntimeError("handler exploded")

    with pytest.raises(ToolExecutionError, match="handler exploded"):
        provider.run(
            Tool(name="boom", input_schema={"required": []}),
            boom,
            {},
            SandboxProfile(name="t"),
            5.0,
        )
    result = provider.run(
        Tool(name="ok", input_schema={"required": []}),
        lambda args: ToolResult(success=True, output="ok"),
        {},
        SandboxProfile(name="t"),
        5.0,
    )
    assert result.success


def test_sandbox_requires_authorization_first(tmp_path):
    """Sandboxing composes with policy; it never replaces it."""
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="danger",
            input_schema={"required": []},
            risk_level=RiskLevel.HIGH,
            requires_sandbox=True,
            sandbox_profile="strict",
        ),
        lambda args: ToolResult(success=True, output="pwned"),
    )
    from ai_ecosystem.security import Policy, PolicyEngine, RiskContext

    authorizer = AuthorizationManager(
        registry,
        policy_engine=PolicyEngine(Policy(name="default")),
        context=RiskContext(root=str(tmp_path)),
    )
    runner = ToolRunner(
        registry,
        authorizer,
        sandbox=LocalSandboxProvider(),
        sandbox_profiles={"strict": SandboxProfile(name="strict")},
    )
    result = runner.run(registry.build_call("t", "danger", {}))
    assert result.success is False
    assert "denied" in result.error


def test_sandbox_fail_closed_without_provider():
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="danger",
            input_schema={"required": []},
            risk_level=RiskLevel.LOW,
            requires_sandbox=True,
        ),
        lambda args: ToolResult(success=True, output="x"),
    )
    runner = ToolRunner(registry, GrantAllAuthorizer())
    result = runner.run(registry.build_call("t", "danger", {}))
    assert result.success is False
    assert "sandbox" in result.error


def test_sandboxed_execution_audited(tmp_path):
    db = Database(":memory:")
    db.migrate()
    try:
        log = AuditLog(db)
        registry = ToolRegistry()
        registry.register(
            Tool(
                name="ok",
                input_schema={"required": []},
                risk_level=RiskLevel.LOW,
                requires_sandbox=True,
            ),
            lambda args: ToolResult(success=True, output="ok"),
        )
        runner = ToolRunner(
            registry,
            GrantAllAuthorizer(),
            sandbox=LocalSandboxProvider(),
            sandbox_profiles={"default": SandboxProfile()},
            auditor=log.as_recorder(),
        )
        assert runner.run(registry.build_call("t9", "ok", {})).success
        records = log.query(task_id="t9")
        assert len(records) == 1
        assert records[0].resource == "ok" and records[0].decision == "GRANTED"
    finally:
        db.close()


def test_audit_record_creation_and_query(tmp_path):
    db = Database(":memory:")
    db.migrate()
    try:
        log = AuditLog(db)
        log.record(
            action="tool.execute",
            actor="a1",
            actor_type="agent",
            task_id="t1",
            resource="filesystem.read",
            decision="GRANTED",
            risk="LOW",
            correlation_id="c1",
        )
        log.record(
            action="tool.execute",
            actor="a1",
            task_id="t2",
            resource="terminal.execute",
            decision="DENIED",
            risk="HIGH",
            correlation_id="c2",
        )
        assert len(log.query(actor="a1")) == 2
        assert len(log.query(task_id="t1")) == 1
        assert len(log.query(action="tool.execute", correlation_id="c2")) == 1
        ok, detail = log.verify()
        assert ok, detail
    finally:
        db.close()


def test_audit_correlation(tmp_path):
    db = Database(":memory:")
    db.migrate()
    try:
        log = AuditLog(db)
        log.record(action="tool.execute", task_id="t1", correlation_id="c1")
        log.record(action="verification", task_id="t1", correlation_id="c1")
        assert len(log.query(correlation_id="c1")) == 2
    finally:
        db.close()


def test_audit_integrity_and_tampering(tmp_path):
    db = Database(":memory:")
    db.migrate()
    try:
        log = AuditLog(db)
        first = log.record(action="tool.execute", actor="a1")
        log.record(action="tool.execute", actor="a1")
        assert log.verify()[0] is True
        db.execute("UPDATE audit_log SET snapshot = ? WHERE id = ?", ('{"forged": true}', first.id))
        ok, offender = log.verify()
        assert ok is False
    finally:
        db.close()


def test_audit_persistence(tmp_path):
    path = str(tmp_path / "audit.db")
    first = Database(path)
    first.migrate()
    AuditLog(first).record(action="tool.execute", actor="a1")
    first.close()
    second = Database(path)
    second.migrate()
    try:
        assert len(AuditLog(second).query(actor="a1")) == 1
        assert AuditLog(second).verify()[0] is True
    finally:
        second.close()


def test_audit_rotation_and_retention(tmp_path):
    db = Database(":memory:")
    db.migrate()
    try:
        log = AuditLog(db)
        log.record(action="tool.execute", actor="a1")
        log.record(action="tool.execute", actor="a2")
        archive = str(tmp_path / "audit.jsonl")
        assert log.rotate(archive) == 2
        assert log.verify()[0] is True
        assert len(log.query()) == 1
        lines = open(archive).read().strip().splitlines()
        assert len(lines) == 2
        assert log.purge_older_than(days=-1, archive_path=archive) >= 0
        assert log.verify()[0] is True
    finally:
        db.close()


def test_runner_auditor_hook_covers_denials(tmp_path):
    from ai_ecosystem.tools import DenyAllAuthorizer

    db = Database(":memory:")
    db.migrate()
    try:
        log = AuditLog(db)
        registry = ToolRegistry()
        registry.register(
            Tool(name="danger", input_schema={"required": []}, risk_level=RiskLevel.HIGH),
            lambda args: ToolResult(success=True),
        )
        runner = ToolRunner(registry, DenyAllAuthorizer(), auditor=log.as_recorder())
        result = runner.run(registry.build_call("t-denied", "danger", {}))
        assert result.success is False
        denied = log.query(task_id="t-denied")
        assert len(denied) == 1
        assert denied[0].decision == "DENIED"
        assert denied[0].risk == "HIGH"
    finally:
        db.close()
