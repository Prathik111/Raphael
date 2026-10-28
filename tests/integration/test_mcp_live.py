"""Real SDK handshakes and calls; no downloads or external services."""

import sys
import json
import socket
import subprocess
import time
import os
from pathlib import Path

from ai_ecosystem.tools.mcp.client import MCPConnection
from ai_ecosystem.tools.mcp.config import ServerConfig


def test_stdio_tools_and_calls_share_an_owned_session(tmp_path):
    script = Path(__file__).resolve().parents[1] / "fixtures/mcp_echo_server.py"
    config = ServerConfig(id="echo", command=sys.executable, args=[str(script)], timeout_s=60)
    connection = MCPConnection(config, tmp_path)
    try:
        definitions = connection.connect()
        assert definitions[0]["name"] == "echo"
        first = connection.request("echo", {"text": "hello"})
        second = connection.request("echo", {"text": "again"})
        assert first["isError"] is False
        assert json.loads(first["content"][0]["text"])["count"] == 1
        assert json.loads(second["content"][0]["text"])["count"] == 2
        assert connection.request("list", {})["content"][0]["text"] == "actual list tool"
    finally:
        connection.close()
    assert not connection.thread.is_alive()


def test_streamable_http_uses_real_sdk_without_double_opening_client(tmp_path):
    script = Path(__file__).resolve().parents[1] / "fixtures/mcp_echo_server.py"
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, str(script), str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    connection = MCPConnection(
        ServerConfig(id="http", transport="http", url=f"http://127.0.0.1:{port}/mcp", timeout_s=60),
        tmp_path,
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            assert process.poll() is None, "test server exited during startup"
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        definitions = connection.connect()
        assert any(tool["name"] == "echo" for tool in definitions)
        result = connection.request("echo", {"text": "http"})
        assert result["isError"] is False
        assert json.loads(result["content"][0]["text"])["text"] == "http"
    finally:
        connection.close()
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            process.terminate()
        process.wait(timeout=10)
