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
