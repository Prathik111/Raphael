# AI Ecosystem — Threat Model (Gate 29)

Applies to the whole system. Each threat names the boundary that stops
it and the regression test that proves it. The invariant underneath
all of them: **no model, skill, agent, cloud, device, message, memory,
or scheduler output ever executes without passing Risk → Policy →
Permission → Authorization → ToolRunner.**

| # | Threat | Boundary | Regression test |
|---|--------|----------|-----------------|
| 1 | Prompt injection via research content | Research stays data; plans validate tools; policy denies dangerous calls | `test_malicious_child.py`, adversarial suite |
| 2 | Malicious model output (rogue plan) | PlanValidator rejects unknown tools; risk/policy deny the rest | `test_adversarial.py`, Gate 7/14 tests |
| 3 | Compromised agent / privilege escalation | Per-agent allow-lists (`agent_scopes`); no permission inheritance | Gate 18 isolation tests |
| 4 | Malicious skill / skill poisoning | Skills build validated plans; risk re-derived from real contracts; approval-gated candidates | Gate 17 dangerous-skill tests |
| 5 | Memory poisoning | Memory is data; zero imports into policy/authorization paths | Gate 12 critical test |
| 6 | Malicious agent message | Typed bounded bus; receivers plan independently; payload stays text | Gate 19 malicious-child test |
| 7 | Workspace injection (XSS) | Closed node vocabulary; executable-content rejection at every depth | Gate 22 XSS tests + frontend scan |
| 8 | Cloud compromise → local action | Providers hold no ToolRunner/policy references; sync is metadata-only | Gate 23 isolation test |
| 9 | Sync poisoning | Conservative default LOCAL_ONLY; secret scan forces FORBIDDEN; explicit conflicts | Gate 24 conflict/secret tests |
| 10 | Phone/hardware compromise | Pairing + HMAC + replay window + command allow-list; approve is advisory, deny cancels | Gates 35-36 tests |
| 11 | Scheduler abuse | Scheduler submits through normal paths; it cannot authorize | Gate 39 policy-rejection test |
| 12 | Credential theft | SecretsProvider + redaction; scans over logs/events/frontend/memory | Gates 23/43 secret tests |
| 13 | Audit tampering | Hash-chained append-only log; linkage-walk verification | Gate 31 tamper test |
| 14 | Sandbox escape | Timeouts/cwd/env/serialization enforced; residual risk documented, policy still applies | Gate 30 escape-composition test |
| 15 | Infinite loops (retry/comms/scheduler) | Bounded retries, hop/TTL limits, idempotency keys, circuit breaker | Gates 8/10/19/39/43 tests |

Trust tiers: UNTRUSTED (external content, new devices) → LIMITED
(approved skills, paired devices, spawned agents) → TRUSTED (operator
components) → SYSTEM (policy assignment only). Tiers add skepticism;
they never grant permissions.
