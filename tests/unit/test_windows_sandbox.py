"""Windows-specific regression tests for the process sandbox boundary."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ai_ecosystem.core.models import Tool, ToolResult
from ai_ecosystem.core.models.enums import RiskLevel
from ai_ecosystem.security.sandbox import LocalSandboxProvider, SandboxProfile


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are only available on Windows")
def test_timeout_kills_worker_and_child(tmp_path: Path) -> None:
    marker = tmp_path / "escaped.txt"

    def handler(_arguments: dict) -> ToolResult:
        subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import time; from pathlib import Path; "
                    f"time.sleep(2); Path({str(marker)!r}).write_text('escaped')"
                ),
                ],
            check=True,
        )
        return ToolResult(success=True, output="child finished")

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

    with pytest.raises(Exception, match="timed out|without a result|sandbox"):
        LocalSandboxProvider().run(tool, handler, {}, profile, timeout_s=0.25)

    time.sleep(0.5)
    assert not marker.exists(), "child process survived the sandbox timeout"
