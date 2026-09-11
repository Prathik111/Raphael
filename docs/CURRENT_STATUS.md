# Current Status — 2026-09-11

## Snapshot

This status describes commit `8a0e7aad2505c81b7d4627a7255ebe4e22f658ef` on `fix/hardened-security-v2`, PR #3 into `main`.

The latest commit is `Export centralized data policy`. It exports `DataClass`, `DataPolicy`, `EgressDecision`, and `require_egress` from the security package. The commit is part of a larger hardening series.

## What is substantially implemented

The repository contains a broad PACE-style agent foundation: durable core state, events, model abstraction/routing, tool registry/runner, FastMCP integration, risk/policy/authorization, structured planning, DAG execution, verification, recovery, research, scoped memory, personality/personalization, a single-agent orchestrator, system observation, learning foundations, skills, multi-agent coordination, desktop/UI scaffolding, cloud abstractions, synchronization, scheduler, interfaces, benchmarks, audit, and packaging helpers.

The security branch additionally hardens:

- Windows worker startup/process supervision
- worker environment isolation
- spawn-safe tool registration
- terminal network denial by default
- root confinement for supported filesystem/git operations
- capability-aware risk
- critical-action approval semantics
- durable semantic action identity
- cancellation/deadline propagation
- cryptographic memory verification receipts
- centralized data classification/egress policy
- privacy-aware model routing
- TLS requirements for remote API exposure
- security regression tests

## What must NOT be assumed

The old `BUILD_PLAN.md` reports a historical 44/44 completion and a historical 604/604 test result from 2026-09-03. Those records describe the prior milestone and are useful history, but they are **not evidence that the current hardened head has been fully tested**.

The current branch also contains implementation breadth that exceeds what has been proven against real external infrastructure. Mock-backed providers, simulated hardware/cloud paths, and operator-built packaging steps must be treated as such.

## Highest-priority unresolved issue

### True OS-level sandbox for arbitrary terminal/code execution

The current Windows hardening uses Job Objects for process/resource/lifetime supervision and tool-level root/network restrictions. Job Objects do not themselves prevent same-user filesystem access outside the workspace. Therefore arbitrary terminal code is not yet fully filesystem-isolated.

A production-grade solution needs an actual OS security boundary, such as a restricted identity/token with appropriate filesystem ACLs and process/network restrictions on Windows, and an appropriate namespace/seccomp/cgroup/container strategy on Linux. The implementation must be empirically tested before the guarantee is documented.

## Other remaining hardening work

1. Run and record the complete CI/security matrix against the current hardened head.
2. Add/strengthen Windows and Linux escape regression tests around the actual OS boundary.
3. Tighten network policy to capability/destination rather than relying only on blanket deny.
4. Continue replay/recovery adversarial testing under crashes and uncertain termination.
5. Continue memory poisoning, secret leakage and data-egress tests.
6. Reconcile stale `BUILD_PLAN.md`/`STRUCTURE.md` language that still describes earlier placeholder states.
7. Separate historical gate completion from current release readiness; release readiness is not established yet.

## Known classes of bugs already discovered during development

The project has previously found and fixed real defects including import cycles, database cursor atomicity races, a scoped-runner authorization gap when `agent_id` was absent, Windows multiprocessing/spawn hazards from non-pickleable handlers, parent environment mutation risks, executor timeout/cancellation honesty problems, replay identity weaknesses, self-asserted memory verification, and contradictory critical-policy semantics.

These are part of the project's engineering history and should guide future testing: security boundaries must be attacked, not merely exercised on the happy path.

## Verification policy for this status file

When updating this file, record exact commands/results when available. Never replace `not run` with `passed`, and never infer real security from static code inspection alone.
