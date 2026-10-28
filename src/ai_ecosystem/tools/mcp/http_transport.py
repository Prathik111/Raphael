"""MCP HTTP transport pins validated DNS results without changing TLS identity."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
from typing import Any

import httpcore
import httpx


class PinnedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, loopback: bool):
        self.loopback = loopback
        self.backend = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ):
        answers = await asyncio.wait_for(
            asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM), timeout
        )
        addresses = list(dict.fromkeys(str(item[4][0]) for item in answers))
        if not addresses or any(
            not (
                ipaddress.ip_address(address).is_loopback
                if self.loopback
                else ipaddress.ip_address(address).is_global
            )
            for address in addresses
        ):
            raise httpcore.ConnectError("MCP host resolves to an unapproved address")
        # The actual TCP connection uses this exact IP, with no second host lookup.
        # HTTPcore still uses the original origin for Host and TLS SNI/certificate checks.
        return await self.backend.connect_tcp(
            addresses[0], port, timeout, local_address, socket_options
        )

    async def connect_unix_socket(self, *args: Any, **kwargs: Any):
        raise httpcore.ConnectError("Unix socket redirects are not supported")

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class ResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream: Any):
        self.stream = stream

    async def __aiter__(self):
        async for chunk in self.stream:
            yield chunk

    async def aclose(self) -> None:
        await self.stream.aclose()


class MCPHTTPTransport(httpx.AsyncBaseTransport):
    def __init__(self, url: str):
        self.origin = httpx.URL(url)
        host = self.origin.host
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        self.pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=PinnedBackend(loopback),
            retries=0,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if (request.url.scheme, request.url.host, request.url.port) != (
            self.origin.scheme,
            self.origin.host,
            self.origin.port,
        ):
            raise httpx.ConnectError("MCP request cannot change its approved origin")
        response = await self.pool.handle_async_request(
            httpcore.Request(
                method=request.method,
                url=httpcore.URL(
                    scheme=request.url.raw_scheme,
                    host=request.url.raw_host,
                    port=request.url.port,
                    target=request.url.raw_path,
                ),
                headers=request.headers.raw,
                content=request.stream,
                extensions=request.extensions,
            )
        )
        return httpx.Response(
            response.status,
            headers=response.headers,
            stream=ResponseStream(response.stream),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self.pool.aclose()
