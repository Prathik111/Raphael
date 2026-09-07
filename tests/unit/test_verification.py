"""Gate 9: deterministic verification incl. mandatory false-success cases."""

import sys

from ai_ecosystem.agent.verifier import Verifier
from ai_ecosystem.core.events import Event, EventBus
from ai_ecosystem.core.models import ToolResult, VerificationResult
from ai_ecosystem.core.models.enums import EventType, VerificationStatus
from ai_ecosystem.core.persistence import Database, SqliteVerificationRepository
from ai_ecosystem.tools import GrantAllAuthorizer, ToolRegistry, ToolRunner, terminal_tools


def _ok(output="done"):
    return ToolResult(task_id="t", tool_call_id="c", success=True, output=output)


def _failed(error="boom"):
    return ToolResult(task_id="t", tool_call_id="c", success=False, error=error)


def test_1_successful_execution_passes(tmp_path):
    (tmp_path / "report.txt").write_text("results")
    verifier = Verifier()
    result = verifier.verify(
        "t", "s1", [_ok()],
        [
            {"strategy": "command_results"},
            {"strategy": "artifact_exists", "params": {"paths": ["report.txt"]}},
            {"strategy": "artifact_properties",
             "params": {"path": "report.txt", "contains": "results"}},
        ],
        root=str(tmp_path),
    )
    assert result.status is VerificationStatus.PASSED
    assert result.task_id == "t" and result.step_id == "s1"
    assert result.evidence, "verdict must carry evidence, not a bare boolean"


def test_2_false_success_command_ok_file_missing_fails(tmp_path):
    verifier = Verifier()
    result = verifier.verify(
        "t", "s1", [_ok()],  # exit 0 -- but nothing was produced
        [{"strategy": "artifact_exists", "params": {"paths": ["absent.txt"]}}],
        root=str(tmp_path),
    )
    assert result.status is VerificationStatus.FAILED
    assert "does not exist" in result.reason


def test_3_missing_build_artifact_fails(tmp_path):
    verifier = Verifier()
    result = verifier.verify(
        "t", "s1", [_ok("build finished")],
        [{"strategy": "artifact_properties",
          "params": {"path": "dist/app.bin", "min_bytes": 10}}],
        root=str(tmp_path),
    )
    assert result.status is VerificationStatus.FAILED


def test_4_corrupt_artifact_fails(tmp_path):
    (tmp_path / "app.bin").write_bytes(b"\x00")  # 1 corrupt byte
    verifier = Verifier()
    result = verifier.verify(
        "t", "s1", [_ok()],
        [{"strategy": "artifact_properties",
          "params": {"path": "app.bin", "min_bytes": 1024}}],
        root=str(tmp_path),
    )
    assert result.status is VerificationStatus.FAILED
    assert "corrupt" in result.reason


def test_5_failed_test_command_fails():
    registry = ToolRegistry()
    for tool, handler in terminal_tools():
        registry.register(tool, handler)
    runner = ToolRunner(registry, GrantAllAuthorizer())
    verifier = Verifier().with_test_command(runner, registry)
    result = verifier.verify(
        "t", "s1", [_ok()],
        [{"strategy": "test_command",
          "params": {"command": [sys.executable, "-c", "raise SystemExit(1)"]}}],
    )
    assert result.status is VerificationStatus.FAILED
    assert "test command reported failure" in result.reason


def test_6_incorrect_output_fails():
    verifier = Verifier()
    result = verifier.verify(
        "t", "s1", [_ok("total: 41")],
        [{"strategy": "command_results",
          "params": {"expect_in_output": "total: 42"}}],
    )
    assert result.status is VerificationStatus.FAILED
    assert "completion criteria" in result.reason


def test_7_no_criteria_is_inconclusive():
    verifier = Verifier()
    result = verifier.verify("t", "s1", [_ok()], [])
    assert result.status is VerificationStatus.INCONCLUSIVE


def test_8_strategy_exception_is_error_not_crash():
    class Exploding:
        name = "exploding"

        def check(self, target, params):
            raise RuntimeError("strategy bug")

    verifier = Verifier(strategies={"exploding": Exploding()})
    result = verifier.verify(
        "t", "s1", [_ok()], [{"strategy": "exploding"}]
    )
    assert result.status is VerificationStatus.ERROR
    assert "raised" in result.reason


def test_unknown_strategy_is_error():
    verifier = Verifier()
    result = verifier.verify("t", "s1", [_ok()], [{"strategy": "telepathy"}])
    assert result.status is VerificationStatus.ERROR


def test_failed_tool_results_fail_command_check():
    verifier = Verifier()
    result = verifier.verify(
        "t", "s1", [_failed("disk full")], [{"strategy": "command_results"}]
    )
    assert result.status is VerificationStatus.FAILED


def test_verification_events_emitted():
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe_all(seen.append)
    verifier = Verifier(bus=bus)
    verifier.verify("t9", "s1", [_ok()], [{"strategy": "command_results"}])
    verifier.verify("t9", "s2", [], [])
    kinds = [e.event_type for e in seen]
    assert kinds[0] is EventType.VERIFICATION_STARTED
    assert EventType.VERIFICATION_PASSED in kinds
    assert EventType.VERIFICATION_INCONCLUSIVE in kinds
    assert all(e.task_id == "t9" for e in seen)


def test_verification_results_persisted():
    db = Database(":memory:")
    db.migrate()
    try:
        repo = SqliteVerificationRepository(db)
        verifier = Verifier(repository=repo)
        created = verifier.verify(
            "t", "s1", [_ok()], [{"strategy": "command_results"}]
        )
        stored = repo.get(created.id)
        assert stored is not None
        assert stored.status is VerificationStatus.PASSED
        assert stored.step_id == "s1"
        assert stored.evidence == created.evidence
    finally:
        db.close()


def test_verifier_is_executor_free():
    """Separation proof: verdicts come from observed results, no executor."""
    import ai_ecosystem.agent.verifier.verifier as module

    imports = [
        line.strip()
        for line in open(module.__file__).read().splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert not any("executor" in line for line in imports), imports
    assert not any("planner" in line and "validator" not in line for line in imports)
    assert not any("intelligence" in line for line in imports), imports
    # Hand-built results verify with no runtime involved at all.
    result = Verifier().verify("t", "s", [_ok()], [{"strategy": "command_results"}])
    assert isinstance(result, VerificationResult)
    assert result.status is VerificationStatus.PASSED


def test_path_escape_in_criteria_fails():
    verifier = Verifier()
    result = verifier.verify(
        "t", "s1", [_ok()],
        [{"strategy": "artifact_exists", "params": {"paths": ["../../etc/passwd"]}}],
        root="/tmp/root",
    )
    assert result.status is VerificationStatus.ERROR
