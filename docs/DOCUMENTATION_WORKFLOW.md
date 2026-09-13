# Documentation and Agent Workflow

## Purpose

Raphael is an evolving autonomous-agent codebase. Documentation is part of the engineering system: it records contracts, security assumptions, progress, failures, decisions and limitations so a new coding agent can continue work without reconstructing the entire project from chat history.

## Source of truth hierarchy

1. Executable behavior and tests
2. Current interfaces/contracts in source
3. Current security and architecture documentation
4. Historical roadmap/build-plan statements
5. Old chat/task descriptions

If two sources disagree, inspect the implementation/tests and update the affected docs rather than preserving contradictory claims.

## Required documentation updates

Every code change must ask: did behavior, architecture, interface, security, tests, progress, known limitations, or roadmap change? If yes, update the appropriate docs in the same change.

At minimum:

- `docs/CURRENT_STATUS.md` for meaningful progress, failures and verification state.
- `docs/ARCHITECTURE.md` for subsystem/control-flow changes.
- `docs/SECURITY_MODEL.md` for trust, authorization, sandbox, data, transport or secret changes.
- `docs/EXECUTION_MODEL.md` for planner/executor/cancellation/recovery/verification changes.
- `AGENTS.md` when a project-wide invariant or agent workflow changes.

## Before implementation

Record or confirm:

- current branch/commit
- affected subsystem
- relevant contract
- security boundary
- existing tests
- known limitations
- whether the requested change is a bug fix, hardening, feature, refactor or documentation-only change

## After implementation

Update:

- tests/regressions
- exact verification commands/results
- current status
- known limitations
- architectural/security docs if applicable
- roadmap/build status only when a completion criterion is actually satisfied

## Never do this

- Do not mark a gate complete because code was written.
- Do not call a mock integration a production integration.
- Do not call static inspection a proof of OS isolation.
- Do not claim CI passed without a recorded run.
- Do not hide a known security limitation because it makes the project description look weaker.
- Do not create a second execution path that bypasses `ToolRunner`.
- Do not let documentation drift silently after implementation.

## Suggested agent report

Every substantial coding task should leave a concise record containing:

```text
Change:
Files:
Behavior changed:
Security impact:
Tests added/changed:
Verification run:
Results:
Known limitations:
Docs updated:
Next recommended step:
```

This repository should remain understandable to a fresh autonomous agent even if all previous chat context is unavailable.
