## Update - 2026-10-28: Centered chat and runtime MCP host

Responses and the composer now share one centered column inside the area beside the sidebar. An isolated browser preview measured identical left edges (405 px) and widths (740 px) at a 1280-pixel viewport, and community preset configuration/save/review was checked in the UI without launching community software.

MCP Settings discovers supported workspace/Cursor/VS Code/Claude/Codex configuration files as inert suggestions and provides Fetch, Memory, Filesystem and custom configurations. Explicit enablement starts an SDK stdio or Streamable HTTP session, discovers tools and registers them for planning. Previously enabled configurations reconnect on restart. Calls require exact-action approval through ToolRunner; pending approvals remain visible across navigation/reload. Valid MCP plans are retained instead of being replaced by conversational fallback, and successful text results appear as external tool responses in chat.

Security properties include bounded non-regex/non-reference schemas with no dialect switches; pre-audit deny guards for egress/known credential values; missing-guard revocation failure; metadata/output sanitization; no direct MCP invocation API; origin-pinned HTTP sockets with validated DNS results; and durable semantic replay identity across reconnects. The trusted Tool.action_identity field separates this identity from reconnect-specific names. MCP subprocesses run with account access and are not an OS sandbox. See MCP.md for protocol support, limits and limitations.

Verification:

- Final selected MCP/transport/domain/desktop/runtime tests: 59 passed. Additional registry/domain selection: 51 passed, 8 deselected. Earlier provider/runtime/hardening selection: 103 passed.
- Real local SDK transport tests: 2 passed for persistent stdio and Streamable HTTP, including repeated calls and a tool named list. Cold Windows imports required a longer fixture startup allowance; an earlier 15-second run timed out. No third-party community package was launched.
- Desktop production build, cargo check --offline, Ruff, targeted MCP mypy (6 files), and final diff whitespace check passed. Whole-source mypy still reports the two unchanged security/sandbox.py typing errors recorded earlier.
- Broader suite before final review fixes: 717 passed, 20 failed, 2 skipped. Failures included existing planner, memory and Windows sandbox/terminal cases, plus a new false positive from scanning downloaded dependency fixtures in .uv-cache. The source scan now excludes that generated cache, matching its existing environment/dependency exclusions, and its focused rerun passed. The whole suite was not repeated after final fixes and is not declared green.

Limitations: tools only (no MCP resources/prompts/sampling), no hosted OAuth/custom authentication headers, no live marketplace index or list-change subscriptions, and no OS-level isolation guarantee. Tool discovery/execution is covered by deterministic and local SDK tests; live Ollama selection of a community MCP tool and third-party hosted integrations were not exercised. Restart the backend/desktop to load new dependencies and MCP routes.

---

## Update - 2026-10-28: Local Ollama inference timeout

The understanding phase inherited the generic 30-second model request default. Ollama now uses a configurable provider timeout (180 seconds by default, range 1-600) when the caller has not explicitly supplied a deadline; explicit positive finite request timeouts remain upper bounds, including subsecond timeouts. Settings exposes Request timeout and Thinking. Thinking defaults off through Ollama's top-level `think` field. Each request also respects the smaller of the saved output limit and the runtime request's token budget. Existing saved settings receive defaults for these new fields.

Desktop result polling now waits up to 15 minutes rather than 90 seconds, so a slow local request does not immediately outlive the UI wait. This is a polling limit, not a task deadline: the final message states that work may continue. Socket timeouts, including URL-wrapped ones, become typed model timeout errors with an actionable Settings hint. No automatic inference retry was added, and a timeout does not prove Ollama stopped generating. Connectivity tests only establish discovery reachability, not inference performance.

Verification: timeout/payload regression tests, runtime/desktop/model-layer checks, Ruff and the desktop production build passed (67 focused tests). A live minimal 16-token request to local `qwen3.5:0.8b` returned nonempty content in 0.92 seconds with thinking off. The exact `request_structured` understanding path for a harmless greeting returned a valid TaskSpec in 36.03 seconds, exceeding the previous 30-second limit. This does not establish cold-load performance or all task completion. Earlier full-suite and mypy failures remain unresolved; those checks were not repeated for this follow-up.

---

## Update - 2026-10-28: Backend startup and Ollama connection testing

Source desktop launches now prefer the checkout's `.venv/Scripts/python.exe` on Windows (`.venv/bin/python` elsewhere) before system Python commands. This fixes source launches where Python is installed only in the project's environment. Startup failures now include the backend log location. System launch fallbacks and authenticated loopback API requirements remain unchanged; packaged Python distribution is still not implemented.

Settings includes a **Test connection** button alongside Refresh, using the authenticated Ollama model-list route. Failed discovery/tests show the endpoint error beside the provider controls; outside Settings, connection failures appear above chat. Save checks connectivity before applying or persisting provider settings. Automatic probes use the applied endpoint when Settings is closed and ignore outdated responses. An offline provider with saved settings is detected on startup or the next connection check; there is no continuous availability monitor.

Verification: three standalone Rust launcher regression tests passed; `cargo check --offline`, desktop `npm run build`, Ruff, and 39 focused provider/runtime/desktop Python tests passed. A temporary backend launched through the project interpreter and returned HTTP 200 on the authenticated Settings route; its process tree was stopped afterward. Runtime tests now stub discovery and clear both endpoint/model environment naming schemes so tests cannot use an ambient Ollama instance. Full-suite and mypy failures from the restoration verification below remain unresolved; the full suite was not repeated for this follow-up. Interactive desktop clicking and real model generation were not tested.

---

## Update — 2026-10-28: Restore desktop model Settings on main

Restored the provider/settings implementation from `origin/feat/ollama-provider-settings` onto `main` after the styling-only revert. Settings now includes automatic installed-model discovery, Refresh, Ollama provider/model selection, endpoint configuration, and temperature, top-p, top-k, repeat penalty, context length, and output length. Authenticated runtime routes apply these options to the active native Ollama provider. The desktop startup compatibility checks from that branch are included so an older managed backend does not silently omit the settings routes.

Only Ollama is selectable. Endpoints are restricted to localhost/loopback because the router treats this provider as local; provider HTTP requests disable environment proxies and redirects. Unsaved form choices do not control chat readiness, and numeric inputs accept default settings. Browser development mode can connect to an explicitly unauthenticated development backend; normal backend authentication remains enforced server-side. Saved non-secret configuration is restored from renderer storage; live inference against an installed Ollama model was not tested.

Verification: `.venv/Scripts/python.exe -m pytest tests/unit/test_ollama_provider.py tests/unit/test_serve_stack.py tests/unit/test_desktop_api.py -q` passed (38 tests); `npm run build`, `cargo check --offline`, and `.venv/Scripts/python.exe -m ruff check src tests` passed. Mypy reports two errors in unchanged `security/sandbox.py` (Windows `os.killpg` and multiprocessing context typing). Final full-suite run with an isolated user-owned temporary folder: 689 passed, 19 failed, 2 skipped, 2 pytest-cache permission warnings. Failures are in unchanged planner/orchestration, DAG timing, sandbox/terminal process execution, memory retention, and scheduler tests. The broader suite is not green. An earlier retry using the shared temporary folder failed at fixture setup because of Windows ownership permissions; isolated-folder results supersede that run. Live Ollama inference and interactive desktop use were not run.

---

## Update — 2026-10-25: Ollama provider configuration

The `feat/ollama-provider-settings` branch adds a single Ollama provider path to the desktop Settings page and runtime API. The desktop can check a loopback Ollama endpoint, list installed model tags, select a model, and save temperature, top-p, top-k, repeat penalty, context length, and output-token limit. The runtime applies these settings to Ollama's native `/api/chat` request and no longer automatically selects LM Studio or llama.cpp as fallback providers. Ollama settings are validated server-side; endpoints must use localhost or loopback IP addresses.

A regression test module was added at `tests/unit/test_ollama_provider.py`. CI for commit `a0ac923dc0f5fe7b3e9ea8e86c2ce90f261411fb` reports Ruff, mypy, desktop production build, and Windows sandbox regression checks passing. Python tests report 701 passed, 3 skipped, and 2 failures in unchanged planner/orchestration tests: `tests/integration/test_single_agent.py::test_18_respond_step_becomes_task_reply` and `tests/unit/test_agent_runtime_regressions.py::test_conversational_goal_uses_direct_response_without_planner_json`. The suite is not fully green.

---

# Current Status — 2026-09-11

## Snapshot

This status describes commit `e7d2c9480dea19f7e3e6cac381cfee48ecd3af9f` on `fix/hardened-security-v2`, PR #3 into `main`.

The documentation commit immediately before this file update established the agent-context documentation layer. The security hardening series' previous code head was `8a0e7aad2505c81b7d4627a7255ebe4e22f658ef`; the latest code change there exported `DataClass`, `DataPolicy`, `EgressDecision`, and `require_egress` from the security package.

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
