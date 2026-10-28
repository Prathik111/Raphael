"""MCP discovery and lifecycle behind operator settings and ToolRunner."""

from __future__ import annotations

import json
import hashlib
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ai_ecosystem.core.models.domain import Tool, ToolResult
from ai_ecosystem.core.models.enums import RiskLevel
from ai_ecosystem.core.secrets import redact, looks_like_secret_value
from ai_ecosystem.security.data_policy import DataPolicy, require_egress
from ai_ecosystem.tools.registry.registry import ToolRegistry
from .client import MCPConnection
from .config import COMMUNITY_SERVERS, ServerConfig, discover_configs


def checked_schema(schema: Any) -> dict:
    if not isinstance(schema, dict) or schema.get("type", "object") != "object":
        raise ValueError("MCP tools require an object input schema")
    if len(json.dumps(schema)) > 64_000:
        raise ValueError("MCP schema exceeds size limit")

    nodes = 0

    def visit(value: Any, depth: int = 0) -> None:
        nonlocal nodes
        nodes += 1
        if depth > 16 or nodes > 512:
            raise ValueError("MCP schema exceeds depth limit")
        if isinstance(value, dict):
            if (
                "$schema" in value
                and value["$schema"] != "https://json-schema.org/draft/2020-12/schema"
            ):
                raise ValueError("MCP schema dialect overrides are not supported")
            if "dependencies" in value:
                raise ValueError("Legacy MCP schema dependencies are not supported")
            if "pattern" in value or "patternProperties" in value:
                raise ValueError("Regex-bearing MCP schemas are not supported")
            if "$ref" in value or "$dynamicRef" in value:
                raise ValueError("External or recursive MCP schema references are forbidden")
            for key, item in value.items():
                if key in {"properties", "dependentSchemas", "$defs", "definitions"} and isinstance(
                    item, dict
                ):
                    for child in item.values():
                        visit(child, depth + 1)
                elif key in {"allOf", "anyOf", "oneOf", "prefixItems"} and isinstance(item, list):
                    for child in item:
                        visit(child, depth + 1)
                elif key in {
                    "items",
                    "contains",
                    "not",
                    "if",
                    "then",
                    "else",
                    "propertyNames",
                    "additionalProperties",
                    "unevaluatedProperties",
                    "unevaluatedItems",
                }:
                    visit(item, depth + 1)

    visit(schema)
    Draft202012Validator.check_schema(schema)
    return schema


class MCPManager:
    def __init__(
        self,
        registry: ToolRegistry,
        db: Any,
        workspace: str,
        connection_factory: Any = MCPConnection,
    ):
        self.registry, self.db, self.workspace = registry, db, Path(workspace)
        self.factory = connection_factory
        self.lock = threading.RLock()
        self.configs: dict[str, ServerConfig] = {}
        self.connections: dict[str, Any] = {}
        self.names: dict[str, list[str]] = {}
        self.states: dict[str, dict] = {}
        self.revisions: dict[str, str] = {}
        self.closed = False
        rows = db.query("SELECT value FROM meta WHERE key=?", ("mcp_servers",))
        if rows:
            for raw in json.loads(rows[0][0]):
                config = ServerConfig.model_validate(raw)
                self.configs[config.id] = config

    def _persist(self) -> None:
        self.db.write(
            "INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)",
            (
                "mcp_servers",
                json.dumps([item.model_dump() for item in self.configs.values()]),
            ),
        )

    def list(self) -> dict:
        with self.lock:
            return {
                "servers": [
                    {
                        "config": config.model_dump(),
                        "status": self.states.get(key, {}).get("status", "disabled"),
                        "error": self.states.get(key, {}).get("error", ""),
                        "tools": list(self.names.get(key, [])),
                    }
                    for key, config in self.configs.items()
                ]
            }

    def discover(self) -> dict:
        found, warnings = discover_configs(self.workspace)
        return {"discovered": found, "warnings": warnings, "community": COMMUNITY_SERVERS}

    def save(self, raw: dict) -> dict:
        config = ServerConfig.model_validate(raw)
        if config.enabled:
            raise ValueError("Save disabled configuration first, then explicitly enable the server")
        with self.lock:
            if self.closed:
                raise ValueError("MCP manager is shutting down")
            if len(self.configs) >= 20 and config.id not in self.configs:
                raise ValueError("At most 20 MCP servers may be configured")
            if self.configs.get(config.id) and self.configs[config.id].enabled:
                raise ValueError("Disable the server before editing its configuration")
            self.configs[config.id] = config
            self._persist()
        return self.list()

    def enable(self, key: str) -> dict:
        with self.lock:
            if self.closed or key not in self.configs:
                raise ValueError("Unknown MCP server or runtime is stopping")
            if self.states.get(key, {}).get("status") == "connecting":
                raise ValueError("MCP server is already connecting")
            self._revoke(key)
            config = self.configs[key].model_copy(update={"enabled": True})
            self.configs[key] = config
            revision = uuid.uuid4().hex[:12]
            self.revisions[key] = revision
            self.states[key] = {"status": "connecting"}
            self._persist()
        connection = self.factory(config, self.workspace)
        try:
            with self.lock:
                if self.closed or self.revisions.get(key) != revision:
                    raise ValueError("MCP connection was revoked")
                self.connections[key] = connection
            definitions = connection.connect()
            if len(definitions) > 128:
                raise ValueError("Too many MCP tools")
            tools = []
            target_digest = hashlib.sha256(
                json.dumps(
                    config.model_dump(exclude={"enabled", "timeout_s"}), sort_keys=True
                ).encode()
            ).hexdigest()
            for index, definition in enumerate(definitions):
                serialized = json.dumps(definition)
                environment_values = getattr(
                    connection,
                    "private_values",
                    [os.environ.get(name, "") for name in config.env_names],
                )
                if looks_like_secret_value(serialized) or any(
                    value and (value in serialized or json.dumps(value)[1:-1] in serialized)
                    for value in environment_values
                ):
                    raise ValueError("MCP tool metadata contains credential data")
                remote_name = definition.get("name")
                if not isinstance(remote_name, str) or not remote_name or len(remote_name) > 200:
                    raise ValueError("Invalid MCP tool name")
                schema = checked_schema(definition.get("inputSchema", {}))
                if definition.get("outputSchema") is not None:
                    checked_schema(definition["outputSchema"])
                slug = re.sub(r"[^a-zA-Z0-9_-]", "_", remote_name)[:50]
                tool = Tool(
                    name=f"mcp.{key}.{revision}.{index}_{slug}",
                    description=f"External MCP tool from {key} (untrusted description): "
                    + str(definition.get("description", ""))[:2000],
                    input_schema=schema,
                    risk_level=RiskLevel.HIGH,
                    requires_approval=True,
                    capabilities=["mcp-external"],
                    network_access=config.transport == "http",
                    action_identity=f"mcp:{key}:{target_digest}:{remote_name}",
                    timeout_s=config.timeout_s + 5,
                )
                tools.append((tool, self._handler(key, revision, remote_name, connection)))
            with self.lock:
                if self.closed or self.revisions.get(key) != revision:
                    raise ValueError("MCP connection was revoked")
                for tool, handler in tools:
                    self.registry.register(tool, handler, argument_guard=self._argument_guard(key, connection))
                self.names[key] = [tool.name for tool, _ in tools]
                self.connections[key] = connection
                self.states[key] = {"status": "connected"}
        except Exception:
            connection.close()
            with self.lock:
                if self.revisions.get(key) == revision:
                    self.states[key] = {
                        "status": "error",
                        "error": getattr(connection, "error", "")
                        or "Could not connect or validate tools. Check the executable, URL and required environment variables, then retry.",
                    }
        return self.list()

    def _argument_guard(self, key: str, connection: Any):
        def guard(arguments: dict) -> str | None:
            encoded = json.dumps(arguments)
            if any(value and (value in encoded or json.dumps(value)[1:-1] in encoded)
                   for value in getattr(connection, "private_values", [])):
                return "MCP arguments contain a configured credential value"
            destination = f"mcp:{key}"
            policy = DataPolicy(approved_sensitive_destinations={destination}, approved_personal_destinations={destination})
            try:
                require_egress(policy, arguments, destination)
            except PermissionError:
                return "MCP arguments violate data-egress policy"
            return None
        return guard

    def _handler(self, key: str, revision: str, remote_name: str, connection: Any):
        def invoke(arguments: dict) -> ToolResult:
            with self.lock:
                if (
                    self.closed
                    or self.revisions.get(key) != revision
                    or not self.configs[key].enabled
                ):
                    return ToolResult(success=False, error="MCP server was disabled or replaced")
            # Enabling a server consents to non-secret argument egress. This never
            # permits secrets, and tool-level action approval is still mandatory.
            destination = f"mcp:{key}"
            encoded_arguments = json.dumps(arguments)
            if any(
                value
                and (value in encoded_arguments or json.dumps(value)[1:-1] in encoded_arguments)
                for value in getattr(connection, "private_values", [])
            ):
                raise PermissionError("MCP arguments contain a configured credential value")
            policy = DataPolicy(
                approved_sensitive_destinations={destination},
                approved_personal_destinations={destination},
            )
            require_egress(policy, arguments, destination)
            response = connection.request(remote_name, arguments)
            if len(json.dumps(response)) > 256_000:
                return ToolResult(success=False, error="MCP result exceeded size limit")

            def clean(value: Any) -> Any:
                if isinstance(value, str):
                    for private in getattr(connection, "private_values", []):
                        value = value.replace(private, "***")
                    return value
                if isinstance(value, dict):
                    return {clean(k): clean(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [clean(item) for item in value]
                return value

            return ToolResult(
                success=not response.get("isError", False),
                output=clean(redact(response)),
                error="MCP server reported a tool error" if response.get("isError") else None,
            )

        return invoke

    def _revoke(self, key: str) -> None:
        self.revisions.pop(key, None)
        for name in self.names.pop(key, []):
            self.registry.unregister(name)
        connection = self.connections.pop(key, None)
        if connection:
            connection.close()

    def disable(self, key: str) -> dict:
        with self.lock:
            if key not in self.configs:
                raise ValueError("Unknown MCP server")
            self._revoke(key)
            self.configs[key] = self.configs[key].model_copy(update={"enabled": False})
            self.states[key] = {"status": "disabled"}
            self._persist()
        return self.list()

    def restore(self) -> None:
        for key, config in list(self.configs.items()):
            if not self.closed and config.enabled:
                self.enable(key)

    def close(self) -> None:
        with self.lock:
            self.closed = True
            for key in list(self.connections):
                self._revoke(key)
