"""One owned MCP session per enabled server, using the official Python SDK."""

from __future__ import annotations

import asyncio
import contextlib
import os
import logging
import shutil
import threading
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path
from typing import Any

from .config import ServerConfig
from .http_transport import MCPHTTPTransport


class MCPConnection:
    def __init__(self, config: ServerConfig, workspace: Path):
        self.config, self.workspace = config, workspace
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.private_values = [
            os.environ[name] for name in config.env_names if os.environ.get(name)
        ]
        self.error = ""
        self.tools: list[dict] = []
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task | None = None
        self.queue: asyncio.Queue | None = None
        self.thread = threading.Thread(target=self._thread, daemon=True, name=f"mcp-{config.id}")

    def connect(self) -> list[dict]:
        if self.stopping.is_set():
            raise RuntimeError("MCP connection was revoked")
        self.thread.start()
        if not self.ready.wait(self.config.timeout_s + 2) or self.error:
            self.error = (
                self.error
                or "MCP connection timed out. Increase the connection timeout or check the executable/server URL."
            )
            self.close()
            raise RuntimeError(self.error)
        return self.tools

    def _thread(self) -> None:
        try:
            asyncio.run(self._run())
        except BaseException:
            self.error = (
                self.error
                or "MCP connection closed or failed. Check server configuration and required environment variables."
            )
            self.ready.set()

    async def _run(self) -> None:
        # Protocol parsing diagnostics can contain untrusted raw payloads.
        logging.getLogger("mcp").setLevel(logging.CRITICAL + 1)
        import httpx
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamablehttp_client

        self.loop = asyncio.get_running_loop()
        self.task = asyncio.current_task()
        self.queue = asyncio.Queue(maxsize=32)
        config = self.config
        if self.stopping.is_set():
            raise RuntimeError("MCP connection was revoked")
        async with AsyncExitStack() as stack:
            if config.transport == "stdio":
                command = shutil.which(config.command) or config.command
                if not Path(command).is_file():
                    self.error = "MCP executable was not found. Install it or enter its full path."
                    raise RuntimeError("MCP executable is not installed")
                base = {
                    name: os.environ[name]
                    for name in (
                        "PATH",
                        "SYSTEMROOT",
                        "WINDIR",
                        "HOME",
                        "USERPROFILE",
                        "APPDATA",
                        "LOCALAPPDATA",
                        "TEMP",
                        "TMP",
                        "LANG",
                    )
                    if name in os.environ
                }
                for name in config.env_names:
                    if name not in os.environ:
                        self.error = (
                            "An MCP environment variable is missing from the backend environment."
                        )
                        raise RuntimeError("Required MCP environment variable is missing")
                    base[name] = os.environ[name]
                # Never copy the parent environment wholesale or log server stderr.
                errlog = stack.enter_context(open(os.devnull, "w"))  # noqa: SIM115 -- owned by ExitStack
                transport = stdio_client(
                    StdioServerParameters(
                        command=command,
                        args=config.args,
                        env=base,
                        cwd=str(self.workspace),
                    ),
                    errlog=errlog,
                )
            else:

                def client_factory(headers: Any = None, timeout: Any = None, auth: Any = None):
                    # The SDK owns and enters this context; never return an already-open client.
                    return httpx.AsyncClient(
                        headers=headers,
                        auth=auth,
                        timeout=timeout or config.timeout_s,
                        trust_env=False,
                        follow_redirects=False,
                        transport=MCPHTTPTransport(config.url),
                    )

                transport = streamablehttp_client(config.url, httpx_client_factory=client_factory)
            streams = await stack.enter_async_context(transport)
            session = await stack.enter_async_context(
                ClientSession(
                    streams[0],
                    streams[1],
                    read_timeout_seconds=timedelta(seconds=config.timeout_s),
                )
            )
            await asyncio.wait_for(session.initialize(), config.timeout_s)
            self.tools = await self._list(session)
            self.ready.set()
            while True:
                method, arguments, result, expires = await self.queue.get()
                await self._dispatch(session, method, arguments, result, expires)

    async def _dispatch(
        self, session: Any, method: str, arguments: dict, result: Any, expires: float
    ) -> None:
        loop = asyncio.get_running_loop()
        if result.done() or loop.time() >= expires or self.stopping.is_set():
            if not result.done():
                result.set_exception(RuntimeError("MCP request expired before execution"))
            return
        try:
            from mcp import types

            # Use the SDK's typed RPC primitive: its call_tool convenience API
            # runs server-provided output schemas on unbounded response data.
            # External results are bounded/redacted by our adapter and remain
            # untrusted evidence, never server-asserted verification.
            response = await asyncio.wait_for(
                session.send_request(
                    types.ClientRequest(
                        types.CallToolRequest(
                            params=types.CallToolRequestParams(name=method, arguments=arguments)
                        )
                    ),
                    types.CallToolResult,
                ),
                expires - loop.time(),
            )
            value = response.model_dump(mode="json", by_alias=True)
            if not result.done():
                result.set_result(value)
        except Exception:
            if not result.done():
                result.set_exception(
                    RuntimeError(
                        "MCP request failed or timed out; work may still be running on the server"
                    )
                )

    async def _list(self, session: Any) -> list[dict]:
        tools: list[dict] = []
        cursor = None
        for _ in range(10):
            page = await asyncio.wait_for(session.list_tools(cursor=cursor), self.config.timeout_s)
            tools.extend(item.model_dump(mode="json", by_alias=True) for item in page.tools)
            if len(tools) > 128:
                raise ValueError("MCP server has too many tools (limit 128)")
            cursor = page.nextCursor
            if not cursor:
                return tools
        raise ValueError("MCP tool discovery exceeded pagination limit")

    def request(self, method: str, arguments: dict) -> Any:
        if self.error or self.loop is None or not self.thread.is_alive():
            raise RuntimeError("MCP server is disconnected; reconnect it in Settings")

        async def enqueue() -> Any:
            result = asyncio.get_running_loop().create_future()
            assert self.queue is not None
            await self.queue.put(
                (
                    method,
                    arguments,
                    result,
                    asyncio.get_running_loop().time() + self.config.timeout_s,
                )
            )
            return await result

        pending = asyncio.run_coroutine_threadsafe(enqueue(), self.loop)
        try:
            return pending.result(self.config.timeout_s + 2)
        except Exception:
            pending.cancel()
            raise RuntimeError(
                "MCP request failed or timed out; server work may continue"
            ) from None

    def close(self) -> None:
        self.stopping.set()
        if self.loop and self.task and self.loop.is_running():
            with contextlib.suppress(RuntimeError):
                self.loop.call_soon_threadsafe(self.task.cancel)
        if self.thread.is_alive():
            self.thread.join(timeout=3)
