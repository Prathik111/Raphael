import asyncio
import socket
from unittest.mock import AsyncMock

import httpcore
import pytest

from ai_ecosystem.tools.mcp.client import MCPConnection
from ai_ecosystem.tools.mcp.config import ServerConfig
from ai_ecosystem.tools.mcp.http_transport import PinnedBackend


@pytest.mark.asyncio
async def test_actual_socket_uses_validated_ip_not_second_dns_resolution(monkeypatch):
    calls = []

    def rebinding_dns(*args, **kwargs):
        calls.append(args[0])
        # A second name lookup would have returned a forbidden address.
        host = "8.8.8.8" if len(calls) == 1 else "169.254.169.254"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", rebinding_dns)
    backend = PinnedBackend(loopback=False)
    backend.backend = AsyncMock()
    await backend.connect_tcp("approved.example", 443, timeout=2)
    assert calls == ["approved.example"]
    assert backend.backend.connect_tcp.call_args.args[0] == "8.8.8.8"


@pytest.mark.asyncio
async def test_remote_transport_rejects_dns_to_private_or_metadata_addresses(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443)),
        ],
    )
    backend = PinnedBackend(loopback=False)
    backend.backend = AsyncMock()
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("approved.example", 443)
    backend.backend.connect_tcp.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_expired_or_cancelled_queued_call_never_reaches_server(tmp_path, cancelled):
    connection = MCPConnection(ServerConfig(id="example", command="example"), tmp_path)
    session = AsyncMock()
    result = asyncio.get_running_loop().create_future()
    if cancelled:
        result.cancel()
    await connection._dispatch(
        session, "write", {"path": "example"}, result, asyncio.get_running_loop().time() - 1
    )
    session.send_request.assert_not_called()
    if not cancelled:
        with pytest.raises(RuntimeError, match="expired"):
            await result
