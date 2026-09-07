# AI Ecosystem — Repository Structure

> Parts 1-30 ✅ Complete (2026-09-03, 44/44 gates): full stack implemented with 604/604 tests passing — `core/` (+config, circuit, lifecycle, secrets), `cloud/` (availability, routing, external providers, offline agent, sync), `security/` (+envelope, sandbox, audit), `learning/` (+pipeline, safety), `agent/` (+proactive, multi, orchestrator), `scheduler/`, `bench/`, `interface/` (+gateway, phone, hardware, voice, protocol), `personalization/`, `skills/`, `system/`, `packaging/`, `desktop/` scaffold, `docs/THREAT_MODEL.md`. SQLite holds 20+ tables (see schema). Details per gate in `BUILD_PLAN.md`.
> This layout is faithful to `VISION.md` § Project Architecture + Technology Stack, and to `BUILD_PLAN.md` Gates 1–3, while following standard industry practices (`src` layout, mirrored `tests/`, separate `desktop/`, `config/`, `scripts/`, `data/`).

## Top-level layout

```text
AiEcosystem/
├── docs/                  # Vision, build plan, structure (this file)
│   ├── VISION.md
│   ├── BUILD_PLAN.md
│   └── STRUCTURE.md
├── src/
│   └── ai_ecosystem/      # Python agent runtime (Tauri desktop is separate in desktop/)
├── desktop/               # Tauri 2 + React + TypeScript app (VISION: Desktop Application)
│   └── src/               # React frontend source (placeholder for Gate 20+)
├── tests/                 # Mirrors src/; unit / integration / e2e (VISION: Testing)
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── scripts/               # Dev/ops helpers (lint, test, migrate, etc.)
├── config/                # Non-secret configuration (placeholders)
└── data/                  # Local runtime data (DB, events). Git-ignored in real use.
```

## VISION.md → directories

| VISION § Project Architecture | Directory | Build gate |
| ----------------------------- | --------- | ---------- |
| Agent Runtime → Execution Context, Goal Understanding | `src/ai_ecosystem/core/models/`, `src/ai_ecosystem/core/runtime/` | Gate 1 |
| Events / logging / errors / persistence (foundation) | `src/ai_ecosystem/core/events/`, `core/errors/`, `core/logging/`, `core/persistence/` | Gates 1–3 |
| Agent Runtime → Orchestrator | `src/ai_ecosystem/agent/orchestrator/` | Gate 15 (placeholder) |
| Agent Runtime → Planner | `src/ai_ecosystem/agent/planner/` | Gate 7 (placeholder) |
| Agent Runtime → Subagents | `src/ai_ecosystem/agent/subagents/` | Gate 15 (placeholder) |
| Agent Runtime → Execution Context / Executor (DAG) | `src/ai_ecosystem/agent/executor/` | Gate 8 (placeholder) |
| Agent Runtime → Risk / Permission Engine | `src/ai_ecosystem/security/policy/` | Gate 6 (placeholder) |
| Agent Runtime → Verifier / Recovery | `src/ai_ecosystem/agent/verifier/`, `agent/recovery/` | Gates 9–10 (placeholders) |
| Intelligence → Model Abstraction / Context | `src/ai_ecosystem/intelligence/models/` | Gate 4 (placeholder) |
| Intelligence → Model Router | `src/ai_ecosystem/intelligence/router/` | Gate 4 (placeholder) |
| Intelligence → Research | `src/ai_ecosystem/intelligence/research/` | Gate 11 (placeholder) |
| Tool Layer → MCP / FastMCP servers | `src/ai_ecosystem/tools/mcp/` | Gate 5 (placeholder) |
| Tool Layer → registry / lifecycle | `src/ai_ecosystem/tools/registry/` | Gate 5 (placeholder) |
| Personalization → Personality | `src/ai_ecosystem/personalization/personality/` | Gate 13 (placeholder) |
| Personalization → Memory / Preferences | `src/ai_ecosystem/personalization/memory/` | Gate 12 (placeholder) |
| Personalization → Skills / Learning | `src/ai_ecosystem/personalization/skills/` | Gates 19, 30 (placeholders) |
| System Awareness → CPU/GPU/RAM/etc. | `src/ai_ecosystem/system/monitor/` | Gates 17–18 (placeholders) |
| Cloud → OCI / Task Queue / Scheduler | `src/ai_ecosystem/cloud/oci/` | Gate 23 (placeholder) |
| Cloud → Sync | `src/ai_ecosystem/cloud/sync/` | Gate 24 (placeholder) |
| Cloud → Compute Router / Kaggle / Lightning | `src/ai_ecosystem/cloud/compute/` | Gates 25–27 (placeholders) |
| Security / Audit (around everything) | `src/ai_ecosystem/security/audit/` | Gates 28–29, 39 (placeholders) |
| Desktop Application (Tauri 2 + React + TS) | `desktop/` | Gates 20–22 (placeholder) |

## Gate 1–3 → directories (active next)

Per `BUILD_PLAN.md` Part 1 / Part 2, the first code to be written lives here — nowhere else:

```text
src/ai_ecosystem/core/models/       # Gate 1: Agent, Task, Goal, Plan, PlanStep, Tool, ToolCall,
                                    #   ToolResult, Permission, RiskAssessment, ExecutionContext,
                                    #   Artifact, Event, Memory, Skill, Model, ModelProvider,
                                    #   Device, ComputeNode, VerificationResult + state machine
src/ai_ecosystem/core/runtime/      # Gate 1: ExecutionContext serialize/restore, task state
src/ai_ecosystem/core/events/       # Gates 1–2: Event, EventBus, EventHandler, EventStore
src/ai_ecosystem/core/errors/       # Gate 1: domain errors
src/ai_ecosystem/core/logging/      # Gate 1: structured logging (feeds audit later)
src/ai_ecosystem/core/persistence/  # Gate 3: repositories (Task, Plan, ExecutionContext,
                                    #   Event, Memory, Skill, Agent) + migrations
tests/unit/                         # Gate 1–3 unit tests (state machine, serialization,
                                    #   event ordering, repository CRUD, crash recovery)
tests/integration/                  # Gate 2–3 integration (bus → store, kill/restart recovery)
```

## Industry-practice notes

* `src` layout: package `ai_ecosystem` is importable without path hacks; `__init__.py` files mark packages.
* Empty future-gate directories contain only `.gitkeep` — intentional. They reserve the VISION mapping without inviting premature implementation (per BUILD_PLAN "Implement ONLY this phase").
* `tests/` mirrors `src/` and splits `unit / integration / e2e` per VISION Testing row.
* `desktop/` is isolated from the Python runtime; they communicate over local IPC/API (VISION Gate 20), so no Python imports from `desktop/` and no TS in `src/`.
* `config/` holds non-secret defaults only. Secrets must never enter prompts, memory, logs, or events (Gate 28 rule) — a future `.env`/secret manager lives outside version control.
* `data/` is runtime state (SQLite, event store). It must be git-ignored once git is initialized.
* Governing rule (BUILD_PLAN Gate 0) applies from day one: model output → policy → tool → verification. No directory may introduce an LLM-to-tool shortcut.

## What is NOT here (deliberately)

* No `pyproject.toml` / `package.json` / Tauri config yet — Part 1 defines contracts first, then packaging (Gate 41).
* No model providers, tools, planner, executor, UI, cloud, or skill code — those are Gates 4+.
* No `.git` — `git init` happens when the user wants version control; `.gitkeep` files already reserve the tree.
