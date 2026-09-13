"""Windows-specific regression tests for the supervised process sandbox."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ai_ecosystem.core.errors.exceptions import ToolTimeoutError
from ai_ecosystem.core.models import Tool, ToolResult
from ai_ecosystem.core.models.enums import RiskLevel
from ai_ecosystem.security.sandbox import LocalSandboxProvider, SandboxProfile
from ai_ecosystem.tools.local.filesystem import filesystem_tools


def _spawn_sleeping_child(arguments: dict) -> ToolResult:
    marker = arguments["marker"]
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import time; from pathlib import Path; "
                f"time.sleep(2); Path({marker!r}).write_text('escaped')"
            ),
        ],
        check=True,
    )
    return ToolResult(success=True, output="child finished")


def _read_test_secret(_arguments: dict) -> ToolResult:
    return ToolResult(success=True, output=os.environ.get("RAPHAEL_TEST_SECRET", "missing"))


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are only available on Windows")
def test_timeout_kills_worker_and_child(tmp_path: Path) -> None:
    marker = str(tmp_path / "escaped.txt")
    tool = Tool(
        name="test.child",
        risk_level=RiskLevel.HIGH,
        capabilities=["subprocess"],
        requires_sandbox=True,
    )
    profile = SandboxProfile(name="windows-test", timeout_s=0.25, max_processes=1)

    with pytest.raises(ToolTimeoutError):
        LocalSandboxProvider().run(
            tool, _spawn_sleeping_child, {"marker": marker}, profile, timeout_s=0.25
        )

    time.sleep(0.5)
    assert not Path(marker).exists(), "child process survived the sandbox timeout"


@pytest.mark.skipif(os.name != "nt", reason="Windows spawn regression is Windows-specific")
def test_registered_filesystem_handler_is_spawn_safe(tmp_path: Path) -> None:
    target = tmp_path / "hello.txt"
    target.write_text("hello", encoding="utf-8")
    tool, handler = filesystem_tools(tmp_path)[0]
    result = LocalSandboxProvider().run(
        tool,
        handler,
        {"path": "hello.txt"},
        SandboxProfile(name="filesystem-test", fs_root=str(tmp_path), timeout_s=2),
        timeout_s=2,
    )
    assert result.success is True
    assert result.output == "hello"


@pytest.mark.skipif(os.name != "nt", reason="Windows spawn regression is Windows-specific")
def test_worker_environment_does_not_inherit_application_secrets(tmp_path: Path) -> None:
    os.environ["RAPHAEL_TEST_SECRET"] = "do-not-leak"
    try:
        tool = Tool(name="test.env", risk_level=RiskLevel.LOW, requires_sandbox=True)
        result = LocalSandboxProvider().run(
            tool,
            _read_test_secret,
            {},
            SandboxProfile(name="env-test", timeout_s=2),
            timeout_s=2,
        )
        assert result.success is True
        assert result.output == "missing"
    finally:
        os.environ.pop("RAPHAEL_TEST_SECRET", None)
