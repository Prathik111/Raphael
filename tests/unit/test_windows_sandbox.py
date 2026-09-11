"""Windows-specific regression tests for the process sandbox boundary."""

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


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are only available on Windows")
def test_timeout_kills_worker_and_child(tmp_path: Path) -> None:
    marker = str(tmp_path / "escaped.txt")
    tool = Tool(
        name="test.child",
        risk_level=RiskLevel.HIGH,
        capabilities=["subprocess"],
        requires_sandbox=True,
    )
    profile = SandboxProfile(
        name="windows-test",
        timeout_s=0.25,
        max_processes=1,
    )

    with pytest.raises(ToolTimeoutError):
        LocalSandboxProvider().run(
            tool,
            _spawn_sleeping_child,
            {"marker": marker},
            profile,
            timeout_s=0.25,
        )

    time.sleep(0.5)
    assert not Path(marker).exists(), "child process survived the sandbox timeout"
