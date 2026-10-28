"""Harmless live MCP fixture for process/session integration tests."""

from mcp.server.fastmcp import FastMCP
import sys

server = FastMCP("Raphael test tools")
count = 0


@server.tool()
def echo(text: str) -> dict:
    """Echo text and count calls in this server session."""
    global count
    count += 1
    return {"text": text, "count": count}


@server.tool(name="list")
def list_items() -> str:
    """A valid tool name must not collide with client operations."""
    return "actual list tool"


if __name__ == "__main__":
    if len(sys.argv) > 1:
        server.settings.host = "127.0.0.1"
        server.settings.port = int(sys.argv[1])
        server.run(transport="streamable-http")
    else:
        server.run(transport="stdio")
