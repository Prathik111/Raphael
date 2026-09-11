import os

import pytest

from ai_ecosystem.core.models.domain import Tool, ToolCall
from ai_ecosystem.core.models.enums import RiskLevel
from ai_ecosystem.security.data_policy import DataClass, DataPolicy
from ai_ecosystem.security.policy.engines import Policy, PolicyEngine, RiskContext, RiskEngine


def test_secret_data_is_never_eligible_for_remote_egress():
    decision = DataPolicy().decide({"api_key": "sk-test-secret"}, "cloud")
    assert decision.classification is DataClass.SECRET
    assert decision.allowed is False


def test_sensitive_data_requires_explicit_destination():
    policy = DataPolicy()
    denied = policy.decide({"email": "user@example.test"}, "cloud")
    assert denied.classification is DataClass.PERSONAL
    assert denied.allowed is False


def test_critical_action_is_approval_gated_not_silently_hard_denied():
    tool = Tool(name="terminal.execute", risk_level=RiskLevel.CRITICAL,
                capabilities=["subprocess", "arbitrary-code-execution"], requires_approval=True)
    call = ToolCall(task_id="t", tool=tool.name, arguments={"command": ["python", "-c", "print(1)"]})
    assessment = RiskEngine().assess("t", tool, call, RiskContext())
    assert assessment.level is RiskLevel.CRITICAL
    allowed, reason = PolicyEngine(Policy()).evaluate(assessment, tool.name)
    assert allowed is False
    assert "requires approval" in reason


def test_secret_environment_variable_is_not_inherited_by_sandbox_helper(monkeypatch):
    monkeypatch.setenv("RAPHAEL_PRIVATE_TEST_SECRET", "secret")
    from ai_ecosystem.security.sandbox import LocalSandboxProvider
    env = LocalSandboxProvider().scrubbed_env()
    assert "RAPHAEL_PRIVATE_TEST_SECRET" not in env
    assert os.environ["RAPHAEL_PRIVATE_TEST_SECRET"] == "secret"
