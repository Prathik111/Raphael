# MCP servers and tools

Raphael hosts MCP tools through the official Python SDK. This extends the existing
tool registry and execution boundary; the model never calls an MCP client directly.

## Desktop workflow

Open **Settings → MCP tools**. Known configuration locations are scanned automatically
when Settings opens; **Discover** repeats the scan. Discovered entries are suggestions,
never permission to execute code. Review an entry or choose a community preset, save
its configuration, and explicitly enable it. Enabling a local server can run/download
software with your account's access. Enabling also consents to sending non-credential
tool arguments to that server; individual calls still require exact-action approval.

Enabled servers are reconnected after a runtime restart. Their tools are listed and
registered automatically, then included in the planner's tool reference for new tasks.
An **Approve once / Deny** panel appears for proposed calls, including calls waiting
when you navigate away or reload. Successful text responses are shown in the chat as
external tool responses. They are evidence, not instructions or proof of external state.

The community presets cover Fetch (`uvx mcp-server-fetch`), Memory, and Filesystem
(`npx -y @modelcontextprotocol/server-memory` and `server-filesystem`). Filesystem
needs an allowed directory added to its arguments. These are starting configurations,
not audited/pinned software releases. You can add other community servers using their
documented executable and a JSON argument list. No package is installed during discovery.

## Discovery locations

- Workspace `.mcp.json`, `.cursor/mcp.json`, and `.vscode/mcp.json`.
- User `.cursor/mcp.json`, `.claude.json`, and `.codex/config.toml`.
- Windows `%APPDATA%/Claude/claude_desktop_config.json` and `Code/User/mcp.json`.

Only supported launch fields are imported. Inline environment values are discarded;
the UI shows environment variable names, whose values must exist in the backend's
environment. Per-project nested configurations and arbitrary configuration paths are
not scanned. Unsupported entries are skipped with a warning. Configuration files
remain untrusted even if another application previously enabled their servers.

## Configuration and transports

Operator configurations are persisted under `mcp_servers` in the runtime database's
existing metadata table. No credential values are persisted in MCP settings.

```json
{
  "id": "my-server",
  "transport": "stdio",
  "command": "uvx",
  "args": ["mcp-server-fetch"],
  "env_names": [],
  "enabled": false,
  "timeout_s": 30
}
```

Supported transports are persistent stdio sessions and Streamable HTTP. A stdio
server runs a direct executable, with a minimal startup environment plus explicitly
named variables. Shell launchers are rejected. Paths and arguments are passed
separately, with the runtime workspace as the working directory. Credentials must
use environment references rather than command-line flags or URL fields.

HTTP accepts loopback HTTP(S) or public HTTPS. Credentials in URLs, query strings,
private/link-local remote addresses, redirects, environment proxies, and origin
changes are rejected. DNS is resolved and validated by the network backend, then
the actual connection dials that literal address; the original hostname remains the
HTTP Host and TLS certificate/SNI identity. OAuth and custom authentication headers
are not implemented, so authenticated hosted integrations may need a local stdio
bridge. Legacy HTTP+SSE, MCP resources, prompts, and sampling are not exposed.

## Execution and revocation

Discovered contracts have a server/revision namespace, HIGH baseline risk, and
mandatory approval. Server annotations cannot lower this policy. Calls pass full
JSON Schema validation, risk/policy/authorization, the durable execution ledger,
ToolRunner, and only then the SDK client. There is no API route for invoking tools.

Dynamic visible names invalidate old approvals after a reconnect. Trusted
`Tool.action_identity` metadata identifies the configured target and remote tool
independently of that name, so reconnecting does not reset semantic replay protection.
Models cannot supply this identity in call arguments. Changing an enabled server
requires disabling it first. Disabling unregisters tools and revokes captured handlers.

Trusted registry deny guards check credential/data policy before request events or approval records; a missing MCP guard fails closed. Secret classification is also checked before argument egress, and configured environment
values are also blocked in arguments and metadata and masked in returned output.
This is defense in depth, not a claim that heuristic secret detection is complete.
Raw server exceptions/stderr are not forwarded to model context or settings responses.

## Limits and honest scope

- At most 20 configurations, 128 tools per server, and 10 discovery pages.
- Schemas: 64 KB, depth 16, at most 512 schema nodes. Regex-bearing schemas and
  schema references, dialect switches, and legacy dependency schemas are unsupported and fail closed, avoiding unbounded regex or
  recursive-reference validation before approval.
- Arguments: 64 KB, depth 16, at most 2,048 nodes and 256 array entries.
- Results: 256 KB; displayed external text is capped at 32,000 characters per result.
- Requests are sequential within a session, with a bounded queue and configurable
  2–120 second timeout. Expired/cancelled queued calls are skipped. A request that
  already reached a server may continue externally after timeout or disconnection.
- Size caps apply after SDK protocol decoding; they are not OS-level memory or CPU isolation.
- Listing occurs at connect/reconnect, not continuously on server list-change events.
  Disable/re-enable to refresh a changed tool catalog. Settings polls status every
  three seconds; the community presets are not a live registry marketplace.
- MCP subprocesses are **not OS-sandboxed**. Approval controls messages sent to them,
  not all behavior of their underlying software. Only enable software you trust.
  The existing repository warning about incomplete arbitrary-code isolation applies.

## Verification

Regression coverage includes inert configuration discovery, validation, schema/secret
rejection, action approvals, revocation, restart/reconnect identity, argument egress,
expired queue entries, DNS pinning, authenticated settings routes, and chat output.
Local SDK integration tests launch a harmless test server over both stdio and HTTP,
exercise repeated calls in one session, and verify a remote tool named `list` works.
Community packages, third-party hosted servers, OAuth, and arbitrary-code isolation
are not established by these tests. See CURRENT_STATUS.md for exact run results.

Protocol and server references: [official SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x),
[reference/community server sources](https://github.com/modelcontextprotocol/servers).
