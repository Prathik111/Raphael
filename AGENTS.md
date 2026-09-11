# Raphael / AI Ecosystem — Agent Operating Instructions

This file is the entry point for autonomous coding agents working on Raphael.

## Mandatory read order

1. `AGENTS.md` (this file)
2. `docs/AGENT_CONTEXT.md`
3. `docs/ARCHITECTURE.md`
4. `docs/SECURITY_MODEL.md`
5. `docs/EXECUTION_MODEL.md`
6. `docs/CURRENT_STATUS.md`
7. `docs/BUILD_PLAN.md` for historical gate intent and roadmap
8. `docs/THREAT_MODEL.md` before security-sensitive changes
9. Relevant tests before changing a subsystem

Then inspect the actual implementation. **Code and tests are authoritative over stale documentation.** If code and docs disagree, do not silently choose one: reconcile the docs in the same change.

## Project identity

Raphael is the repository/product name. The Python package is currently named `ai_ecosystem`, with project metadata describing it as a personal AI operating layer.

The goal is a persistent, model-independent personal AI system that can understand goals, plan, use controlled tools, execute work, verify external state, recover from failure, retain useful memory, personalize behavior, and eventually operate across desktop/cloud/device nodes.

The immediate engineering priority is the **agent core and trustworthy execution boundary**, not adding more surface features.

## Non-negotiable invariants

- The model proposes; it does not authorize.
- No LLM output may directly invoke an executable tool.
- Tool calls pass schema validation, risk/policy/authorization, then the `ToolRunner` choke point.
- Tool output, research content, files, model output, and memory are untrusted data unless independently verified/promoted.
- Memory is evidence/context, never an authorization source.
- Personality/preferences never grant permissions.
- Verification determines reality; exit code or model claims are not proof of success.
- Authorization and sandboxing are separate boundaries.
- Cancellation must not be represented as successful termination when work may still be running.
- Replay protection must survive process restarts and must not rely only on model-controlled IDs.
- Security failures fail closed.
- Secrets must not enter prompts, memory, skills, logs, events, or model context.
- Data egress is classification/policy controlled.
- Remote interfaces must not silently downgrade security for convenience.

## Documentation rule

**Every future repository change must update documentation when the change affects architecture, security, behavior, status, tests, interfaces, limitations, or roadmap.** At minimum update `docs/CURRENT_STATUS.md` and the relevant subsystem document. If a new invariant or architectural decision is introduced, update `AGENTS.md` and/or add an ADR.

Do not claim a feature is complete merely because code exists. Record what was implemented, what was tested, what was not tested, and known limitations.

## Safe working procedure

1. Inspect the current branch and commit.
2. Read this documentation set and the relevant implementation/tests.
3. Identify the exact boundary being changed.
4. Preserve existing contracts unless deliberately changing them.
5. Add regression tests for the failure/security property being fixed.
6. Run the narrow tests first, then the broader suite and static checks available in the environment.
7. Re-read the diff for security bypasses, stale docs, and accidental scope expansion.
8. Update documentation in the same change.
9. Report exact verification results; distinguish `passed`, `not run`, and `not available`.

## Current security warning

The hardened branch improves policy, replay, cancellation, memory trust, data classification, transport, and Windows process supervision. It **does not yet constitute a complete OS-level filesystem sandbox for arbitrary terminal commands**. Windows Job Objects control process lifetime/resources, not arbitrary filesystem permissions. Linux namespace/seccomp/cgroup isolation is also not a completed guarantee. Do not describe the current sandbox as a full security boundary for arbitrary untrusted code.
