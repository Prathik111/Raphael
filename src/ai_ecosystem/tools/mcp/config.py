"""Operator-owned MCP configuration. Discovered configuration never enables code."""

from __future__ import annotations

import ipaddress
import json
import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_ecosystem.core.secrets import looks_like_secret_value


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,48}$")
    transport: Literal["stdio", "http"] = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list, max_length=64)
    url: str = ""
    env_names: list[str] = Field(default_factory=list, max_length=32)
    enabled: bool = False
    timeout_s: int = Field(default=30, ge=2, le=120)

    @field_validator("env_names")
    @classmethod
    def environment_names(cls, names: list[str]) -> list[str]:
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,100}", name) for name in names):
            raise ValueError("Environment entries must be variable names, not values")
        blocked = {
            "PYTHONPATH",
            "PYTHONHOME",
            "NODE_OPTIONS",
            "LD_PRELOAD",
            "DYLD_INSERT_LIBRARIES",
        }
        if any(name.upper() in blocked for name in names):
            raise ValueError("Runtime injection environment variables are not supported")
        return sorted(set(names))

    @model_validator(mode="after")
    def validate_target(self) -> ServerConfig:
        texts = [self.command, self.url, *self.args]
        if any(len(value) > 4096 or any(ord(c) < 32 for c in value) for value in texts):
            raise ValueError("MCP launch fields contain invalid characters or are too long")
        if any(looks_like_secret_value(value) for value in texts):
            raise ValueError("Use environment variable names for credentials; never embed values")
        if any(
            re.match(r"^--?(api[-_]?key|token|password|secret|authorization)(=|$)", value, re.I)
            for value in self.args
        ):
            raise ValueError(
                "Credential command-line flags are not supported; use environment variable names"
            )
        if self.transport == "stdio":
            if not self.command.strip() or self.url:
                raise ValueError("stdio requires a command and no URL")
            if Path(self.command).stem.lower() in {
                "cmd",
                "powershell",
                "pwsh",
                "bash",
                "sh",
                "zsh",
            }:
                raise ValueError("Use a direct MCP executable, not a shell interpreter")
        else:
            if self.command or self.args or self.env_names:
                raise ValueError("HTTP requires a URL; local launch fields must be empty")
            parsed = urlsplit(self.url)
            host = parsed.hostname or ""
            _port = parsed.port  # Invalid ports must fail configuration validation.
            try:
                address = ipaddress.ip_address(host)
            except ValueError as exc:
                if host != "localhost" and not re.fullmatch(
                    r"[A-Za-z][A-Za-z0-9.-]*\.[A-Za-z]{2,}", host
                ):
                    raise ValueError("Invalid MCP host") from exc
                loopback = host == "localhost"
            else:
                loopback = address.is_loopback
                if not loopback and not address.is_global:
                    raise ValueError(
                        "Non-loopback private/link-local MCP endpoints are not supported"
                    )
            if parsed.scheme not in {"http", "https"} or not host:
                raise ValueError("MCP URL must be HTTP(S)")
            if parsed.scheme != "https" and not loopback:
                raise ValueError("Remote MCP servers require HTTPS")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("MCP URLs cannot contain credentials, a query or a fragment")
        return self


def discover_configs(workspace: Path, home: Path | None = None) -> tuple[list[dict], list[str]]:
    """Read known config locations as data only; never start discovered commands."""
    home = home or Path.home()
    paths = [
        workspace / ".mcp.json",
        workspace / ".cursor/mcp.json",
        workspace / ".vscode/mcp.json",
        home / ".cursor/mcp.json",
        home / ".claude.json",
        home / ".codex/config.toml",
    ]
    if os.environ.get("APPDATA"):
        paths.append(Path(os.environ["APPDATA"]) / "Claude/claude_desktop_config.json")
        paths.append(Path(os.environ["APPDATA"]) / "Code/User/mcp.json")
    found, warnings = [], []
    seen = set()
    for path in paths:
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > 1_000_000:
                raise ValueError("oversized configuration")
            if path.suffix == ".toml":
                try:
                    import tomllib
                except ImportError:
                    import tomli as tomllib  # type: ignore[no-redef]
                payload = tomllib.loads(path.read_text(encoding="utf-8"))
                entries = payload.get("mcp_servers", {})
            else:
                payload = json.loads(path.read_text(encoding="utf-8"))
                entries = payload.get("mcpServers", payload.get("servers", {}))
            if not isinstance(entries, dict):
                raise ValueError("invalid server map")
            for name, raw in list(entries.items())[:50]:
                if not isinstance(raw, dict) or name in seen:
                    continue
                try:
                    config = ServerConfig(
                        id=name,
                        transport="http" if raw.get("url") else "stdio",
                        command=raw.get("command", ""),
                        args=raw.get("args", []),
                        url=raw.get("url", ""),
                        env_names=list(raw.get("env", {})),
                    )
                    found.append({"config": config.model_dump(), "source": str(path)})
                    seen.add(name)
                except (ValueError, TypeError):
                    warnings.append(
                        f"Skipped an unsupported server in {path.name}; enter its configuration manually."
                    )
        except (OSError, ValueError, AttributeError):
            warnings.append(f"Could not read {path.name}.")
    return found, warnings


COMMUNITY_SERVERS = [
    {
        "name": "Fetch",
        "description": "Fetch public web pages.",
        "url": "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
        "config": {
            "id": "fetch",
            "transport": "stdio",
            "command": "uvx",
            "args": ["mcp-server-fetch"],
        },
    },
    {
        "name": "Memory",
        "description": "A local knowledge graph.",
        "url": "https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
        "config": {
            "id": "memory",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-memory"],
        },
    },
    {
        "name": "Filesystem",
        "description": "File access within the directory you specify. Add that directory to Arguments before enabling.",
        "url": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
        "config": {
            "id": "filesystem",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        },
    },
]
