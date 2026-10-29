# Architecture — Current Implementation

## Scope

This document describes the architecture as implemented on the documented hardened branch snapshot. It separates implemented contracts from long-term vision.

## Core lifecycle

```text
USER GOAL
  |
  v
PERSONAL / TASK CONTEXT
  |
  v
PLANNER -> PLAN VALIDATOR
  |
  v
CAPABILITY + RISK ANALYSIS
  |
  v
POLICY / AUTHORIZATION / APPROVAL
  |
  v
TOOL RUNNER
  |
  +--> local tools
  +--> FastMCP-backed tools
  +--> controlled workers
  |
  v
OBSERVED RESULT / ARTIFACT
  |
  v
VERIFIER
  |
  +--> VERIFIED
  +--> FAILED
  +--> INCONCLUSIVE / ERROR
  |
  v
RECOVERY / REPLAN / ESCALATION
  |
  v
PERSISTENCE + AUDIT + MEMORY
```

The single-agent orchestrator is the preferred default. Multi-agent facilities exist as bounded extensions; they are not a reason to bypass the shared security/execution path.

## Package responsibilities

### `core`
Stable domain contracts and infrastructure. It owns durable task/execution state, events, errors, configuration, lifecycle/backup facilities, and cross-cutting primitives.

### `agent/planner`
Transforms reasoning output into a structured plan and validates tool names, dependencies, criteria and plan shape before execution.

### `agent/executor`
Converts a validated plan to a dependency graph and schedules runnable nodes. It never becomes an alternate direct-to-tool path: execution goes through `ToolRunner`.

### `agent/verifier`
Independent post-execution assessment. It should not trust executor success flags or model claims as proof of external state.

### `agent/recovery`
Classifies failures, applies bounded retry/replan policy and escalates when the system cannot safely establish the desired state.

### `agent/orchestrator`
Coordinates the PACE-style single-agent lifecycle over persistence, planning, policy, execution, verification, recovery, memory and personalization.

### `tools/registry` and `tools/mcp`
Define tool metadata and adapters. FastMCP is an integration mechanism, not an authorization boundary.

### `security`
The security choke points: capability/trust envelope, risk/policy/authorization, approval, sandbox provider, audit, and data classification/egress policy.

### `intelligence`
Model-provider abstraction/router and research pipeline. Model selection must respect capability and privacy/data-classification constraints.

The desktop currently exposes Ollama as its only selectable model provider. Settings flow through the authenticated RuntimeAPI to the active provider/router; installed model tags are discovered from the configured Ollama server, and generation controls are sent as native Ollama chat options. The endpoint validator restricts all endpoints to localhost or loopback IP addresses; provider requests disable environment proxies and redirects. Additional providers and automatic fallback are intentionally not selectable until their settings and privacy behavior are implemented.

### `personalization`
Structured personality/preferences plus scoped persistent memory. These are context and behavior inputs, never authorization inputs.

### `skills`
Versioned skill discovery/candidate handling. Generated or updated skills require validation and appropriate approval before gaining privileges.

### `cloud`
Provider abstractions, availability, routing, external execution, offline/cloud behavior and synchronization. Cloud is an extension of the same policy and verification model, not a security bypass.

### `interface`
Runtime API and gateway plus phone/hardware/voice/protocol adapters and UI-facing event projections.

### `desktop`
Tauri 2 + React + TypeScript client. It is separate from the Python runtime and communicates through the interface layer.

## Data/control flow rules

### Model output
Model output is untrusted. Parse it into typed structures; validate before it can influence an action.

### Tool output
Tool output is also untrusted. It can contain prompt injection or misleading claims. Use it as evidence and feed it to verification rather than allowing it to alter policy.

### Memory
Retrieved memory is context, not authority. It must not grant permissions, approve actions, or override current policy.

### Research
External content is evidence with provenance/confidence, not executable instruction.

### UI
UI requests must enter the same runtime authorization path. A frontend affordance is not authorization by itself.

## Persistence and restart

SQLite is the current durable foundation. Execution context and job/task state are persisted so restart recovery can determine what was pending/completed/interrupted. Semantic action identity is designed to survive process restart and prevent duplicate execution based solely on a model-controlled call ID.

## Concurrency

The DAG executor can run independent steps concurrently. Cancellation tokens propagate from parent to child and deadlines can request cancellation. Python threads cannot be force-killed safely by the executor; therefore a timeout is not proof that arbitrary in-process side effects have stopped. Future changes must preserve honest state reporting.

## Event architecture

Events are used for runtime observability, persistence, UI projection and audit integration. Event consumers must not be allowed to mutate security policy merely by observing an event.

## Long-term vs current implementation

The vision includes OCI persistence, external GPU compute, phones, hardware, voice, proactive behavior, self-learning, dynamic UI and a broad ecosystem protocol. Many of these have scaffolds/implementations in the repository, but external-provider production operation and installer/toolchain artifacts are not automatically proven by the presence of code. Treat mock-backed paths as mock-backed until a real integration is explicitly verified.

## Architectural decision rule

A new feature should extend an existing boundary rather than create a parallel path. In particular:

```text
NEW TOOL -> registry -> risk/policy -> authorization -> ToolRunner -> verification
NEW MODEL -> provider contract -> privacy-aware router
NEW MEMORY -> scoped store -> trust/verification rules
NEW REMOTE NODE -> authenticated transport -> policy -> job -> verification
NEW UI ACTION -> RuntimeAPI/gateway -> same security boundary
```

## Source desktop backend startup

When the source checkout exists, the Tauri launcher prefers its `.venv` interpreter and sets the repository working directory and source import path. System Python commands remain fallback candidates. Model connection tests use the existing authenticated discovery route and do not send a prompt; desktop Save checks connectivity before applying settings. This is a source-development launch improvement, not a bundled Python installer.

## Local inference budgets

Ollama's provider settings supply a default 180-second socket timeout when ModelRequest has no explicitly supplied timeout. An explicit caller timeout can shorten that budget. The native payload includes the selected thinking mode and caps output by both the saved limit and the per-request token budget. These are inference controls, not authorization or termination guarantees. Desktop polling has a separate 15-minute limit and does not cancel backend work on expiry.

## Runtime MCP host

`tools/mcp` now includes operator configuration discovery, persistent SDK sessions
(stdio/Streamable HTTP), and a lifecycle manager. Configurations live in the runtime
metadata table; discovered files are inert suggestions. Explicit enablement connects
a server and registers namespaced tools with mandatory action approval. New tasks
see the current registry. ToolRunner still owns execution and durable identity;
trusted `Tool.action_identity` separates semantic replay identity from reconnect
names that invalidate stale approvals. HTTP uses a DNS-pinning network backend.
See [MCP.md](MCP.md) for contracts, limits, and unimplemented authentication features.

The desktop uses a single centered conversation column inside the area beside the
sidebar. Response and composer widths/offsets share CSS variables at each breakpoint.
Pending approvals are polled independently of local submission state.

The runtime composition root registers `web.search` as a bounded public search
adapter. Search requests remain ordinary registered tool calls, so policy,
authorization, audit, timeout, and egress classification apply before any
network request. Search results are untrusted source data and are fed into the
existing research pipeline rather than treated as instructions.
For a completed search step, the single-agent reply path requests a separate
model synthesis over bounded snippets and appends observed source links. The
result API includes bounded source metadata for the desktop's selected-chat
publisher list. This synthesis does not authorize further tools.
The understanding phase can identify a task-specific knowledge gap and focused
query. The research phase runs the proposed search through ToolRunner and
passes attributed snippets to the planner so it can continue the original
task, with normal policy and approval applied to the search action.
After exhausted planner repair on an explicit search request, a fixed search
plan is validated against registered tools. This recovers from truncated local
model JSON without granting tool authority or falling back for other actions.
Standalone local-date requests use one shared recognizer in understanding and
planning. Primary-agent research and planned `web.search` steps use a separate
bounded query-formulation model call over the original task and information gap.
Intermediate model instructions are not used as the search query. Formulated
queries are checked for known meta-instructions, dropped task acronyms, and
unsupported years, with one correction attempt. The resulting query enters
normal tool authorization and approval. Failure to formulate a valid query
stops the task before search. These checks do not prove semantic relevance.

The same composition root registers `agent.delegate` and a scoped research
specialist. Delegation is approval-gated and creates a child task with a
bounded plan; the child uses a separate allow-list-scoped runner and its result
is returned as untrusted data. The parent cannot delegate arbitrary executable
text or expand the child's authority.
