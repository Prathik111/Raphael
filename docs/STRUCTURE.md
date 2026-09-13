# Raphael — Repository Structure (Current)

> Snapshot: `fix/hardened-security-v2` at commit `396190048f51b519854afc6a896eab723fc76734` (documentation update built on `8a0e7aad2505c81b7d4627a7255ebe4e22f658ef`).
>
> This document describes the repository as it exists now. Historical gate status belongs in `BUILD_PLAN.md`; current implementation/security limitations belong in `AGENT_CONTEXT.md`, `CURRENT_STATUS.md`, `ARCHITECTURE.md`, and `SECURITY_MODEL.md`.

## Top-level layout

```text
Raphael/
├── AGENTS.md                       # mandatory instructions for coding agents
├── docs/
│   ├── VISION.md                   # long-term product vision
│   ├── BUILD_PLAN.md               # historical/development gate record
│   ├── STRUCTURE.md                # this current repository map
│   ├── THREAT_MODEL.md              # threat categories and security assumptions
│   ├── AGENT_CONTEXT.md             # autonomous-agent onboarding/context
│   ├── ARCHITECTURE.md              # current architecture and boundaries
│   ├── SECURITY_MODEL.md            # current security guarantees/limitations
│   ├── EXECUTION_MODEL.md           # planning/execution/verification/recovery
│   ├── CURRENT_STATUS.md             # current progress, failures, verification
│   └── DOCUMENTATION_WORKFLOW.md    # rules for keeping docs synchronized
├── src/ai_ecosystem/               # Python runtime/package
├── tests/                           # unit, integration, e2e and security tests
├── desktop/                         # Tauri 2 + React + TypeScript client
├── packaging/                       # first-run, backup/upgrade, Windows release helpers
├── config/                          # non-secret configuration placeholders
└── .github/workflows/ci.yml         # automated CI definition
```

## Python runtime map

```text
src/ai_ecosystem/
├── core/             # domain contracts, runtime state, persistence, events, lifecycle
├── agent/            # planner, executor, verifier, recovery, orchestrator, multi-agent
├── intelligence/     # models/router and research
├── tools/             # registry, ToolRunner, MCP/local integrations
├── security/          # policy/risk/auth, trust, sandbox, audit, data policy
├── personalization/  # memory, personality, preferences
├── learning/         # observation, learning proposals and safety controls
├── skills/            # skill discovery/versioning/validated candidates
├── system/            # system/resource observation
├── cloud/             # availability, routing, sync, external/offline paths
├── interface/         # RuntimeAPI/gateway and device/UI adapters
├── scheduler/        # durable scheduler
└── bench/            # benchmark support
```

## Critical boundary map

| Boundary | Current owner | Rule |
|---|---|---|
| Model → action | planner + validator + security | Model output is untrusted and cannot directly execute. |
| Action → tool | `security` + `tools/registry/runner.py` | Risk/policy/authorization must precede execution. |
| Tool/MCP → runtime | ToolRunner | MCP is not a security bypass. |
| Execution → truth | `agent/verifier` | Observable state, not model claims, determines success. |
| Failure → next action | `agent/recovery` | Retry/replan is bounded and must account for uncertain termination. |
| Memory → agent | `personalization/memory` | Memory is evidence/context, not authority. |
| Data → remote provider | `security/data_policy` + routing | Egress must respect data classification/privacy. |
| UI/device → runtime | `interface` | UI affordances do not bypass runtime policy. |
| Remote API | `interface`/gateway + transport policy | Remote exposure must use appropriate authenticated/secure transport. |

## Important status note

The repository contains implementations across the broad 44-gate vision, but **"implemented in the repository" is not equivalent to "production-proven."** Some integrations are mock/simulation-backed and the current hardened branch still has an unresolved true OS-level filesystem isolation problem for arbitrary terminal/code execution. See `docs/CURRENT_STATUS.md` and `docs/SECURITY_MODEL.md`.

## Documentation ownership

Every future change that affects behavior, architecture, security, tests, progress, interfaces, limitations, or roadmap must update the corresponding documentation in the same change. `AGENTS.md` and `DOCUMENTATION_WORKFLOW.md` make this a project rule.
