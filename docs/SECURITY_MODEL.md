# Security Model — Hardened Branch

## Security objective

Raphael must remain safe even when the model, tool output, files, research content, memory, or subagent is malicious or compromised. Security therefore cannot depend on the model behaving correctly.

## Authorization pipeline

```text
Model proposal
 -> typed plan/tool call
 -> capability analysis
 -> risk engine
 -> policy engine
 -> authorization
 -> explicit approval when required
 -> durable action identity
 -> ToolRunner
 -> sandbox/worker controls
 -> actual tool
 -> observed result
 -> independent verification
 -> audit
```

No stage may be skipped for convenience.

## Capability risk

Risk is derived from what an operation can do, its arguments/target/scope and policy context. Do not reduce risk to a string match on the tool name or command text. New capabilities must receive an explicit risk treatment.

Critical actions must be approval-gated according to policy. A policy that both requires approval and unconditionally denies the same class is contradictory; implementation must make the intended decision explicit.

## Trust boundaries

### Model boundary
Assume model output is attacker-controlled. Prompt instructions do not constitute permission.

### Tool/MCP boundary
FastMCP or another tool protocol cannot bypass Raphael's registry, policy, authorization, runner, audit and verification layers.

### Memory boundary
Memory can be poisoned. A `verified` field supplied by the memory producer is not an authoritative verification receipt. Promotion requires independent verification/receipt semantics.

### Data boundary
Data classes are centralized. Sensitive/secret information must not be sent to a model/provider or remote worker unless the applicable egress policy explicitly permits it.

### Remote boundary
Remote APIs and nodes must use authenticated, appropriately secure transport. A remote bind must not silently fall back to plaintext HTTP.

## Secrets

Secrets are treated differently from ordinary data. Detection/sanitization is defensive, not a proof that no secret exists. Do not log, persist, embed in prompts, or route secrets merely because a sanitizer failed to recognize them.

## Sandbox

### What is implemented

The hardened Windows path supervises worker processes with Job Objects before untrusted handler execution, constrains resources, cleans up descendants, minimizes the worker environment, restricts supported filesystem/git tools to an allowed root, and makes terminal networking deny-by-default.

### What is NOT implemented

A Job Object is primarily a process/resource/lifetime mechanism. It does **not** by itself prevent a same-user process from opening arbitrary filesystem paths. Therefore arbitrary terminal/code execution is not yet a complete OS-level filesystem sandbox.

Likewise, Linux namespace/seccomp/cgroup isolation is not yet a completed production guarantee. Do not claim cross-platform arbitrary-code isolation until it is implemented and tested.

## Replay and idempotency

The semantic action identity is based on the task/tool/arguments rather than trusting a model-generated call ID. Durable reservation is required so process restart does not erase the replay boundary. Unknown/in-flight work must be reconciled rather than blindly repeated.

## Cancellation and timeout

Cancellation is a request, not magic process termination. For in-process Python threads, the runtime cannot safely kill an already-running thread. A timed-out/cancelled action must therefore not be reported as safely terminated unless the underlying execution boundary can establish termination.

## Audit

Security-relevant actions are recorded in an append-only/hash-chained audit mechanism. Audit evidence should allow reconstruction of actor, action, policy/risk decision, authorization/approval, execution outcome and verification status.

## Required security tests for changes

At minimum consider:

- prompt/tool injection
- path traversal and absolute-path escape
- command injection
- unauthorized capability use
- critical-policy contradictions
- replay after restart
- cancellation while side effects are active
- memory poisoning / fake verification
- secret leakage through logs/prompts/results
- prohibited data egress
- remote plaintext transport
- compromised subagent behavior
- sandbox escape attempts

## Security completion standard

A security fix is not complete because a unit test passes. For security boundaries, verify the actual external property where practical and document environment-specific limitations. Never convert a simulation or mock result into a claim of real OS/cloud security.
