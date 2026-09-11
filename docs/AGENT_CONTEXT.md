# Agent Context — Raphael / AI Ecosystem

## Snapshot

- Repository: `Prathik111/Raphael`
- Branch documented here: `fix/hardened-security-v2`
- Snapshot commit: `ed21a2da771860b3ee586a3b5692c393a3db0f78`
- Snapshot date: 2026-09-11
- Pull request: #3, base `main`
- Base commit: `73ce38c324043ee0408a1656c64e4efd2a185e42`
- PR state at snapshot: open, not merged
- Python package/project: `ai_ecosystem` / `ai-ecosystem`, version `0.1.0`
- Python requirement: >=3.10
- Development dependencies include pytest, pytest-asyncio, `ruff==0.6.9`, and mypy.

## What Raphael is trying to become

A persistent personal AI operating layer, not a chatbot demo. The long-term system surrounds interchangeable models with persistent identity/personality/memory/skills, planning, policy, controlled computer interfaces, verification, recovery, distributed compute, desktop UI, and future cloud/device nodes.

The central engineering thesis is:

> **The model provides intelligence; the runtime provides agency and control.**

A model can be replaced without replacing the agent's durable state or security policy.

## Current architecture in one view

```text
User goal
  -> agent/orchestrator
  -> planner + plan validator
  -> capability/risk analysis
  -> policy + authorization + approval
  -> ToolRunner
  -> controlled tool / MCP / worker
  -> observed result/artifact
  -> independent verifier
  -> recovery/reconciliation when needed
  -> durable state + audit + memory
```

Around this pipeline are persistence, events, data classification, model routing, cancellation/deadlines, sandbox/process controls, and interfaces.

## Repository map

`src/ai_ecosystem/core/` — domain models, runtime state, persistence, events, configuration, lifecycle, circuit breaker, secrets, backup support.

`src/ai_ecosystem/agent/` — planner, executor/DAG, verifier, recovery, orchestrator/single-agent loop, multi-agent coordination, proactive behavior.

`src/ai_ecosystem/intelligence/` — model abstraction/routing and research.

`src/ai_ecosystem/tools/` — registry, runner, local tools and FastMCP integration.

`src/ai_ecosystem/security/` — policy/risk/authorization, trust/capability envelope, sandbox provider, audit, centralized data policy.

`src/ai_ecosystem/personalization/` — memory, personality and preferences.

`src/ai_ecosystem/learning/` — usage observation, learning proposals, safety/governor controls.

`src/ai_ecosystem/skills/` — versioned skill registry/discovery/validated candidates.

`src/ai_ecosystem/system/` — system/resource observation.

`src/ai_ecosystem/cloud/` — availability, routing, external providers, offline/cloud behavior, sync.

`src/ai_ecosystem/interface/` — Runtime API, gateway, phone/hardware/voice/protocol adapters and UI event views.

`src/ai_ecosystem/scheduler/` — durable scheduling.

`src/ai_ecosystem/bench/` — benchmark definitions/runner.

`desktop/` — Tauri 2 + React + TypeScript application.

`tests/` — unit/integration/e2e/security/regression coverage.

`docs/` — project vision, historical build plan, structure, threat model and this agent-context layer.

`packaging/` — first-run, backup/restore/upgrade helpers and Windows installer/build configuration.

## Runtime contracts

### Planner
Produces structured plans, not arbitrary executable text. Plans contain steps, dependencies, tools, risk/verification information and completion criteria. Plans are validated before execution.

### ToolRunner
The mandatory execution choke point. Tools are registered with schemas/capabilities/policies and are executed only after the security path authorizes them. MCP must not bypass this boundary.

### Risk/policy
Risk is capability/argument/scope aware rather than merely trusting tool names. Critical actions are approval-gated according to policy. Policy is not an LLM suggestion.

### Executor
`TaskGraph` + `ParallelExecutor` schedule dependency-aware work. Independent nodes may run concurrently. Cancellation/deadlines are cooperative for Python threads; a timeout cannot magically kill arbitrary Python work.

### Verification
Verification is separate from execution and must inspect observable state/artifacts. Supported result semantics distinguish success from failure/inconclusive/error. A successful command is not by itself proof of desired behavior.

### Recovery
Failure classification, bounded retries, replanning gates and escalation operate on observed execution state. Permission failures must not be blindly retried.

### Persistence
SQLite is the current durable store. Execution state and repositories support restart/crash recovery. Semantic action identity is durable so replay defense is not dependent on an in-memory set or model-generated call ID.

## Security model summary

1. Trust boundaries are explicit.
2. Untrusted model/tool/research/memory content cannot directly authorize actions.
3. Capability risk is evaluated before execution.
4. Approval is tied to an authorized action, not a vague conversation state.
5. Secrets are sanitized and data egress is centrally classified.
6. Remote API exposure requires secure transport for remote binds.
7. Audit records are hash-chained.
8. Sandbox/process/resource controls are fail-closed where their provider cannot establish the requested boundary.

### Important limitation

The current Windows sandbox hardening supervises worker processes with Job Objects and constrains supported tool paths, environment, resources, and terminal network behavior. **A Windows Job Object is not a filesystem ACL boundary.** An arbitrary terminal process running with the user's existing identity can still potentially access paths outside the workspace. Therefore the project must not claim that arbitrary terminal code is fully filesystem-isolated yet. Linux OS isolation is likewise not a completed production guarantee.

## Current hardening work

PR #3 is the current hardening line. It addresses:

- supervised Windows sandbox startup
- worker environment minimization
- spawn-safe registered handlers
- terminal network deny-by-default
- root-confined supported filesystem/git operations
- capability-aware risk
- approval-gated critical operations
- durable semantic action identity/replay defense
- parent/child cancellation and deadlines
- independent memory verification receipts
- centralized data classification/egress decisions
- privacy-aware model routing
- TLS requirements for remote API exposure
- security regression tests

The latest code commit before the documentation-only commits was `8a0e7aad2505c81b7d4627a7255ebe4e22f658ef` (`Export centralized data policy`). The current branch head now includes the documentation layer and structure/status synchronization.

## Known issues / unfinished security work

P0: implement a real OS-level boundary for arbitrary untrusted terminal/code execution. On Windows this likely requires a restricted identity/token plus filesystem ACLs and other OS controls; on Linux it requires an appropriate namespace/seccomp/cgroup strategy. The exact implementation must be designed and tested rather than assumed.

P1: make the full CI/security matrix execute on the hardened branch and record results. GitHub workflow presence is not evidence that the latest commit has passed CI.

P1: strengthen network isolation to the minimum required destinations/capabilities rather than treating network denial as a universal substitute for sandboxing.

P1: continue adversarial testing around secrets, data classification, remote transport, replay/recovery, memory poisoning, and compromised-model behavior.

P2: reconcile stale roadmap statements in `BUILD_PLAN.md` that describe historical gate completion versus current release readiness.

## Historical progress

The project was built through the documented 44-gate plan. Earlier progress records reported 604/604 tests at the 2026-09-03 checkpoint. That number is historical, not a claim about the current hardened commit. The hardened branch added/changed security behavior after that checkpoint; current verification must be rerun against the actual head.

The project has repeatedly found real issues while hardening: import cycles, database cursor races, scoped-runner authorization gaps, Windows spawn hazards, parent environment mutation hazards, cancellation semantics that could outlive the caller, replay identity weakness, self-asserted memory verification, and contradictions in critical-action policy. These are examples of why tests and adversarial review are required before declaring a gate complete.

## How another agent should reason about changes

Before coding, determine whether the change affects: authorization, execution, persistence, trust, verification, data egress, transport, concurrency, or recovery. Those are security-sensitive boundaries.

Do not solve a security problem by moving it into a lower layer and assuming the lower layer is safe. Trace the complete path from model input to external side effect and back to verification.

When changing an interface, search all callers and tests before editing. When changing persistence, consider restart and duplicate execution. When changing cancellation, consider work that already escaped into a thread/process. When changing memory or research, assume the content is attacker-controlled. When changing cloud/model routing, classify the data before it leaves the local boundary.
