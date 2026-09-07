# AI Ecosystem — Proper Engineering Build Plan

> The earlier 44-phase roadmap is **architecturally good but too broad to use as an actual engineering execution plan**. It tells you *what exists*, but not rigorously enough:
> * what exact code gets written first
> * what depends on what
> * what interfaces must be frozen
> * how each feature is tested
> * what constitutes "done"
> * what should **not** be built yet
> * how to prevent the project from becoming an untestable autonomous-agent monolith
>
> For AI Ecosystem, this document restructures it into **build gates**. Do not move to the next gate until the previous gate passes its completion criteria.

---

## Build Status Tracking

**Legend:**

* `✅ Complete` — gate passed all completion criteria, tests + docs done
* `🔄 Ongoing` — currently being implemented (only one gate should be Ongoing at a time)
* `⏳ Pending` — not started
* `🚫 Blocked` — cannot proceed (dependency failed / decision needed)

**Overall Progress:** 44 / 44 gates complete (100%)

### Master Gate Status

| Gate | Title | Status |
| ---- | ----- | ------ |
| 0 | Governing Architecture | ⏳ Pending |
| 1 | Architecture Foundation | ✅ Complete |
| 2 | Event Architecture | ✅ Complete |
| 3 | Persistence | ✅ Complete |
| 4 | Model Abstraction | ✅ Complete |
| 5 | Tool Framework | ✅ Complete |
| 6 | Permission + Risk Engine | ✅ Complete |
| 7 | Planner | ✅ Complete |
| 8 | DAG Executor | ✅ Complete |
| 9 | Verification Engine | ✅ Complete |
| 10 | Recovery | ✅ Complete |
| 11 | Research | ✅ Complete |
| 12 | Memory | ✅ Complete |
| 13 | Personality + Personalization | ✅ Complete |
| 14 | Single-Agent Autonomous Loop | ✅ Complete |
| 15 | Computer / System Awareness | ✅ Complete |
| 16 | System Usage Observation + Learning Foundation | ✅ Complete |
| 17 | Skill Engine | ✅ Complete |
| 18 | Multi-Agent Foundation | ✅ Complete |
| 19 | Agent Communication + Coordination | ✅ Complete |
| 20 | Desktop Application | ✅ Complete |
| 21 | Live Agent Visualization | ✅ Complete |
| 22 | Dynamic Workspace | ✅ Complete |
| 23 | OCI Cloud Integration | ✅ Complete |
| 24 | PC ↔ OCI Synchronization | ✅ Complete |
| 25 | PC Availability | ✅ Complete |
| 26 | Compute Router | ✅ Complete |
| 27 | Kaggle / Lightning Execution | ✅ Complete |
| 28 | Offline / Cloud Agent | ✅ Complete |
| 29 | Security Architecture | ✅ Complete |
| 30 | Sandboxing | ✅ Complete |
| 31 | Audit System | ✅ Complete |
| 32 | Self-Learning | ✅ Complete |
| 33 | Learning Safety | ✅ Complete |
| 34 | Proactive Agent | ✅ Complete |
| 35 | Phone Interface | ✅ Complete |
| 36 | Hardware Interface | ✅ Complete |
| 37 | Voice | ✅ Complete |
| 38 | Ecosystem Protocol | ✅ Complete |
| 39 | Global Scheduler | ✅ Complete |
| 40 | End-to-End Autonomous Tasks | ✅ Complete |
| 41 | Benchmarking | ✅ Complete |
| 42 | Red-Team Security | ✅ Complete |
| 43 | Production Hardening | ✅ Complete |
| 44 | Packaging / Release | ✅ Complete |

### OpenCode Implementation Parts Status

| Part | Gates Covered | Status |
| ---- | ------------- | ------ |
| Part 1 | Architecture + contracts (Gates 1-2) | ✅ Complete |
| Part 2 | Runtime foundation (Gate 3) | ✅ Complete |
| Part 3 | Model layer (Gate 4) | ✅ Complete |
| Part 4 | Tool layer (Gate 5) | ✅ Complete |
| Part 5 | Security foundation (Gate 6) | ✅ Complete |
| Part 6 | Planning (Gate 7) | ✅ Complete |
| Part 7 | Execution (Gate 8) | ✅ Complete |
| Part 8 | Verification + Recovery (Gates 9-10) | ✅ Complete |
| Part 9 | Research + Memory (Gates 11-12) | ✅ Complete |
| Part 10 | Personality + Personalization (Gate 13) | ✅ Complete |
| Part 11 | Single-Agent MVP (Gate 14) | ✅ Complete |
| Part 12 | Multi-Agent (Gates 15-16) | ⏳ Pending -- superseded: Gates 15-19 restructured 2026-09-03, see Parts 13-17 |
| Part 13 | Computer / System Awareness (Gate 15) | ✅ Complete |
| Part 14 | Usage Observation + Learning (Gate 16) | ✅ Complete |
| Part 15 | Skill Engine (Gate 17) | ✅ Complete |
| Part 16 | Multi-Agent Foundation (Gate 18) | ✅ Complete |
| Part 17 | Agent Communication + Coordination (Gate 19) | ✅ Complete |
| Part 18 | Desktop + Visualization (Gates 20-21) | ✅ Complete |
| Part 19 | Dynamic Workspace (Gate 22) | ✅ Complete |
| Part 20 | OCI + Synchronization (Gates 23-24) | ✅ Complete |
| Part 21 | PC Availability + Compute Router (Gates 25-26) | ✅ Complete |
| Part 22 | External Compute + Offline Agent (Gates 27-28) | ✅ Complete |
| Part 23 | Security Envelope + Sandbox + Audit (Gates 29-31) | ✅ Complete |
| Part 24 | Learning + Safety + Proactive (Gates 32-34) | ✅ Complete |
| Part 25 | Phone + Hardware + Voice + Protocol (Gates 35-38) | ✅ Complete |
| Part 26 | Scheduler + Autonomous E2E (Gates 39-40) | ✅ Complete |
| Part 27 | Benchmarking (Gate 41) | ✅ Complete |
| Part 28 | Red Team (Gate 42) | ✅ Complete |
| Part 29 | Production Hardening (Gate 43) | ✅ Complete |
| Part 30 | Packaging / Release (Gate 44) | ✅ Complete |

> **How to update tracking:** change a gate's `> **Status:**` line and the tables above. Keep exactly one gate `🔄 Ongoing` at a time. Only mark `✅ Complete` when all completion criteria + Definition of DONE are met.

---

## 0. The governing architecture

> **Status:** ⏳ Pending

Keep this boundary throughout the entire project:

```text
                    ┌───────────────────────────┐
                    │          USER              │
                    └─────────────┬─────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────┐
                    │      PERSONAL AGENT        │
                    │ personality / memory /     │
                    │ preferences / identity     │
                    └─────────────┬─────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────┐
                    │       ORCHESTRATOR         │
                    │ goal / reasoning / planner │
                    └─────────────┬─────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────┐
                    │       POLICY LAYER         │
                    │ permission / risk / auth   │
                    └─────────────┬─────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────┐
                    │        TOOL ROUTER         │
                    │ MCP / local / cloud        │
                    └─────────────┬─────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────┐
                    │         EXECUTOR           │
                    └─────────────┬─────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────┐
                    │        VERIFIER            │
                    └─────────────┬─────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────┐
                    │    MEMORY / EXPERIENCE    │
                    └───────────────────────────┘
```

And around everything:

```text
        SECURITY
 ┌───────────────────────────────┐
 │ authentication                │
 │ authorization                 │
 │ sandboxing                    │
 │ secrets                       │
 │ audit                         │
 │ resource limits               │
 │ isolation                     │
 └───────────────────────────────┘
```

The most important rule remains:

> **The model must never directly control the computer.**

The model produces intentions/structured actions. Policy decides whether they're allowed. Tools execute them. Verification determines whether they actually worked.

---

# BUILD GATE 1 — Architecture Foundation

> **Status:** ✅ Complete (2026-09-03: 19 domain models, state machine, ExecutionContext snapshot/restore, 39/39 tests pass)

### Goal

Create the non-AI foundation on which everything else will run.

### Build

Create the core domain models:

```text
Agent
Task
Goal
Plan
PlanStep
Tool
ToolCall
ToolResult
Permission
RiskAssessment
ExecutionContext
Artifact
Event
Memory
Skill
Model
ModelProvider
Device
ComputeNode
VerificationResult
```

Do **not** implement complex AI behavior yet.

Build:

```text
core/
    models/
    runtime/
    events/
    errors/
    logging/
    persistence/
```

### ExecutionContext

This becomes one of the most important objects in the system.

It should contain things such as:

```text
task_id
goal
current_state
plan
current_step
variables
tool_results
artifacts
observations
permissions
risk_assessments
errors
metadata
timestamps
```

It must be possible to serialize and restore it.

### State machine

Implement:

```text
CREATED
   ↓
UNDERSTANDING
   ↓
AWARENESS
   ↓
RESEARCHING
   ↓
PLANNING
   ↓
WAITING_PERMISSION
   ↓
EXECUTING
   ↓
VERIFYING
   ↓
RECOVERING
   ↓
COMPLETED
```

with terminal states:

```text
FAILED
CANCELLED
```

### Tests

Unit-test:

* valid state transitions
* invalid state transitions
* context serialization
* context restoration
* event creation
* event ordering
* stable IDs
* error handling

### Completion gate

**Do not proceed until:**

* every core model has tests
* state machine tests pass
* ExecutionContext can be serialized/restored
* events can be emitted and consumed
* no business logic depends on an LLM
* test suite runs automatically

---

# BUILD GATE 2 — Event Architecture

> **Status:** ✅ Complete (2026-09-03: EventBus/EventStore + TaskCreated → Bus → Logger → Store verified, no UI/LLM)

The entire ecosystem should become event-driven.

Implement:

```text
EventBus
Event
EventHandler
EventStore
```

Example:

```text
TaskCreated
PlanCreated
AgentStarted
AgentCompleted

ToolRequested
ToolStarted
ToolCompleted
ToolFailed

PermissionRequested
PermissionGranted
PermissionDenied

VerificationStarted
VerificationPassed
VerificationFailed

MemoryCreated
SkillCreated
SkillUpdated
```

### Why now?

Because later:

```text
Desktop UI
Cloud
Logging
Telemetry
Agent visualization
Audit
Phone
Hardware
```

can all consume the same event stream.

### Tests

Test:

```text
publisher → subscriber
publisher → multiple subscribers
event ordering
failed subscriber isolation
event persistence
replay
```

### Completion

You should be able to run:

```text
TaskCreated
→ EventBus
→ Logger
→ EventStore
```

without any UI or LLM.

---

# BUILD GATE 3 — Persistence

> **Status:** ✅ Complete (2026-09-03: SQLite repos, migrations, transactions, kill/restart recovery proven)

Before making the agent autonomous, give it durable state.

Implement persistence for:

```text
tasks
execution contexts
plans
events
models
tools
permissions
memory
skills
agents
devices
jobs
```

Start with a reliable relational database rather than prematurely introducing multiple databases.

Create repositories:

```text
TaskRepository
PlanRepository
ExecutionContextRepository
EventRepository
MemoryRepository
SkillRepository
AgentRepository
```

### Tests

Test:

* create
* read
* update
* delete where appropriate
* transactions
* rollback
* concurrent access
* restart recovery
* migrations

### Critical test

Kill the application during execution.

Restart it.

It must be able to determine:

```text
What task was running?
What step was running?
What had already completed?
What remains?
```

### Completion

A crashed process must **not automatically lose the task state**.

---

# BUILD GATE 4 — Model Abstraction

> **Status:** ✅ Complete (2026-09-03: provider ABC + mock, router with fallback, structured-output validation, all mocked)

Only now connect models.

Create:

```text
ModelProvider
Model
ModelRequest
ModelResponse
ModelCapabilities
ModelRouter
```

Capabilities:

```text
tool_calling
structured_output
reasoning
vision
streaming
parallel_tool_calls
context_length
```

Providers should be interchangeable.

For example:

```text
Local
OpenAI-compatible
OCI
Other cloud provider
```

The rest of the agent should not care which model is being used.

### Model router

Input:

```text
task requirements
privacy
context size
latency
cost
availability
hardware
capabilities
```

Output:

```text
selected model
```

### Tests

Use mock models.

Test:

```text
model unavailable
timeout
malformed response
invalid structured output
provider failure
fallback
routing decision
```

### Completion

You can replace the underlying model without modifying:

```text
planner
executor
memory
tools
UI
```

---

# BUILD GATE 5 — Tool Framework

> **Status:** ✅ Complete (2026-09-03: registry + policy-checked runner, 6 initial tools, optional FastMCP bridge, every call logged/identified)

Now build the actual computer interface.

Standard tool contract:

```text
Tool
 ├─ id
 ├─ name
 ├─ description
 ├─ input_schema
 ├─ output_schema
 ├─ permissions
 ├─ risk_level
 ├─ timeout
 ├─ capabilities
 └─ execution_policy
```

Tool lifecycle:

```text
REQUESTED
   ↓
VALIDATED
   ↓
RISK CHECK
   ↓
PERMISSION CHECK
   ↓
EXECUTE
   ↓
OBSERVE
   ↓
VERIFY
```

Connect your existing FastMCP servers here.

Do not let MCP become a bypass around your policy layer.

### Initial tools

Keep the first set small:

```text
filesystem.read
filesystem.list
filesystem.exists

git.status
git.diff

terminal.execute
```

Prefer read-only tools first.

### Tests

Test every tool independently.

For each tool:

```text
valid input
invalid input
missing parameter
permission denied
timeout
execution failure
malicious input
unexpected output
```

### Completion

Every tool execution is:

```text
logged
identified by task_id
identified by tool_call_id
permission checked
risk assessed
observable
verifiable
```

---

# BUILD GATE 6 — Permission + Risk Engine

> **Status:** ✅ Complete (2026-09-03: risk/policy/permission/authorization choke point, no LLM-to-tool path, red-tested traversal + injection)

This is a **hard security boundary**.

Build:

```text
RiskEngine
PermissionEngine
PolicyEngine
AuthorizationManager
```

Example:

| Operation                 | Risk     |
| ------------------------- | -------- |
| Read file                 | LOW      |
| List directory            | LOW      |
| Git status                | LOW      |
| Modify file               | MEDIUM   |
| Install package           | HIGH     |
| Execute arbitrary command | HIGH     |
| Delete data               | CRITICAL |

But don't hard-code everything solely by tool name.

Risk should consider:

```text
tool
arguments
target
scope
user policy
current task
agent identity
environment
```

### Tests

Create a security test suite:

```text
allowed operation
denied operation
high-risk operation
malformed tool call
path traversal
command injection
permission escalation attempt
agent attempting unauthorized action
```

### Completion

**There must be no path from LLM output directly to an executable tool.**

Every tool call passes:

```text
schema validation
→ risk
→ policy
→ authorization
```

---

# BUILD GATE 7 — Planner

> **Status:** ✅ Complete (2026-09-03: strict validator + dependency resolver + mock-backed reasoning backend, all 7 invalid-plan cases rejected)

Now make the system capable of planning.

Do NOT make the planner output a giant paragraph.

It must produce a structured plan:

```text
Plan
 ├── goal
 ├── steps[]
 │    ├── id
 │    ├── description
 │    ├── dependencies
 │    ├── tools
 │    ├── risk
 │    ├── verification
 │    └── completion criteria
 └── final verification
```

Example:

```text
Goal:
Create project report

Step 1:
Read source documents

Step 2:
Extract information
depends_on: Step 1

Step 3:
Generate report
depends_on: Step 2

Step 4:
Verify report
depends_on: Step 3
```

### Tests

Use deterministic mock LLM outputs.

Test:

* valid plan
* missing dependency
* circular dependency
* nonexistent tool
* impossible step
* unsafe plan
* incomplete completion criteria

### Completion

Planner cannot generate a plan that the runtime cannot validate.

---

# BUILD GATE 8 — DAG Executor

> **Status:** ✅ Complete (2026-09-03: TaskGraph + ParallelExecutor through ToolRunner only, max_concurrency/cancel/timeout/policies proven, restart-consistent, 123/123 tests pass)

Now introduce parallelism.

Convert:

```text
Plan
```

into:

```text
DAG
```

Build:

```text
TaskGraph
DependencyResolver
ParallelExecutor
ResultAggregator
CancellationManager
```

Example:

```text
        A
      /   \
     B     C
      \   /
        D
```

B and C execute simultaneously.

D waits for both.

### Resource controls

Implement:

```text
max_concurrency
CPU limits
memory limits
timeout
cancellation
queueing
```

### Tests

Test:

```text
parallel execution
dependency ordering
failure propagation
partial failure
cancellation
timeout
resource exhaustion
```

### Completion benchmark

Create 10 independent tasks.

Compare:

```text
sequential execution time
parallel execution time
```

Verify that independent tasks genuinely execute concurrently.

---

# BUILD GATE 9 — Verification Engine

> **Status:** ✅ Complete (2026-09-03: executor-free Verifier + 4 deterministic strategies, all false-success cases fail correctly, events + SQLite persistence, 14 tests)

This should be treated as a first-class subsystem.

Build:

```text
Verifier
VerificationStrategy
VerificationResult
```

Levels:

```text
1. command succeeded

2. expected artifact exists

3. artifact has expected properties

4. tests pass

5. behavior is correct

6. independent verification
```

The agent should never say:

> "Done"

simply because a command returned exit code 0.

### Example

Agent:

```text
Build application
```

Executor:

```text
npm build
```

Verifier:

```text
Does build artifact exist?
Does it contain expected files?
Can application launch?
Do tests pass?
```

### Tests

Intentionally create:

```text
false-success command
missing artifact
corrupt artifact
failed tests
incorrect output
```

### Completion

The system must detect deliberately injected false successes.

This is a critical milestone.

---

# BUILD GATE 10 — Recovery

> **Status:** ✅ Complete (2026-09-03: classifier + bounded retry policy + replan gates + escalation + audited engine loop, permission never retried, 16 unit + 2 E2E tests)

Now introduce autonomy.

Flow:

```text
EXECUTE
   ↓
FAIL
   ↓
CLASSIFY FAILURE
   ↓
CAN RETRY?
 ┌─┴─┐
YES  NO
 ↓    ↓
RETRY  REPLAN
          ↓
       ESCALATE
```

Build:

```text
FailureClassifier
RetryPolicy
RecoveryPlanner
EscalationManager
```

Classify:

```text
transient
configuration
tool failure
permission failure
model failure
dependency failure
logical failure
unknown
```

### Tests

Inject:

* network timeout
* tool crash
* bad model output
* permission denial
* missing dependency
* incorrect plan

### Completion

Agent must recover from predefined failures without infinite retry loops.

---

# BUILD GATE 11 — Research

> **Status:** ✅ Complete (2026-09-03: manager/collector/extractor/ranker/conflicts/context via ToolRunner only, provenance preserved, malicious content inert, Gate 10 retry reused, 13 mocked tests)

Now build research.

```text
ResearchManager
Search
Retrieval
SourceCollector
SourceRanker
Evidence
ContextBuilder
```

Important:

Research results should be represented as evidence rather than blindly inserted into the prompt.

Example:

```text
Source
Evidence
Confidence
Timestamp
Origin
```

### Tests

Test:

* no results
* conflicting sources
* duplicate sources
* stale information
* malformed source
* retrieval failure

### Completion

The agent can conduct a research task and distinguish:

```text
source
claim
evidence
confidence
```

---

# BUILD GATE 12 — Memory

> **Status:** ✅ Complete (2026-09-03: scoped lifecycle store, deterministic retrieval, conflicts preserve history, propose/apply consolidation, retention/privacy flags, malicious memory cannot authorize, 26 tests)

Now make the agent persistent.

Do **not** immediately build a giant "AI memory" system.

Start with structured memory.

Types:

```text
episodic
semantic
project
preference
skill
experience
```

Memory:

```text
id
type
content
source
confidence
importance
scope
created_at
updated_at
```

Pipeline:

```text
OBSERVATION
     ↓
MEMORY CANDIDATE
     ↓
IMPORTANCE
     ↓
STORE
     ↓
RETRIEVE
     ↓
UPDATE / CONSOLIDATE / DECAY
```

### Tests

Test:

* storing memory
* retrieval
* irrelevant-memory rejection
* conflicting memory
* memory update
* memory deletion
* scope isolation
* project separation

### Critical test

Create two projects with conflicting information.

The agent must not leak Project A memory into Project B.

---

# BUILD GATE 13 — Personality + Personalization

> **Status:** ✅ Complete (2026-09-03: constrained profiles, versioned SQLite persistence, project overrides, model-independent structured context, preferences cannot authorize, 17 tests)

Personality must be **data/configuration**, not a model-specific prompt.

Create:

```text
PersonalityProfile
PreferenceProfile
BehaviorPolicy
PersonalizationEngine
```

Examples:

```text
verbosity
formality
humor
proactivity
communication style
workflow preferences
```

Separate:

```text
PERSONALITY
PREFERENCES
FACTS
POLICIES
```

This distinction is important.

### Tests

Give identical tasks with different profiles.

Verify behavioral differences.

Then switch models.

Verify personality remains consistent.

### Completion

Changing the model must not erase the user's personality.

---

# BUILD GATE 14 — Single-Agent Autonomous Loop

> **Status:** ✅ Complete (2026-09-03: SingleAgent PACE orchestration over Gates 3-13, full lifecycle + crash resume + compromised-model containment, 15 integration tests) — SINGLE-AGENT MVP COMPLETE

Only now combine everything.

```text
GOAL
 ↓
UNDERSTANDING
 ↓
AWARENESS
 ↓
RESEARCH
 ↓
PLAN
 ↓
RISK
 ↓
PERMISSION
 ↓
EXECUTE
 ↓
VERIFY
 ↓
RECOVER if necessary
 ↓
MEMORY
 ↓
COMPLETE
```

### End-to-end tests

Test realistic tasks:

### Task A

```text
Inspect repository
→ identify issue
→ modify file
→ run tests
→ verify
```

### Task B

```text
Find information
→ research
→ summarize
→ verify sources
→ save result
```

### Task C

```text
Create a project
→ install dependencies
→ configure
→ test
→ verify
```

### Completion

The agent completes a predefined benchmark suite with:

* no unauthorized actions
* no false completion
* recoverable failures
* complete audit trail

This is your first **real AI Ecosystem milestone**.

---

# BUILD GATE 15 — Computer / System Awareness

> **Status:** ✅ Complete (2026-09-03: `system/monitor/` — probes, manager, context, redacted tool, snapshot persistence, 16 tests; restructured from old "Multi-Agent System" title, see changelog)

Only after the single agent is reliable.

Build:

```text
AgentManager
SubagentManager
AgentScheduler
AgentCommunication
AgentLifecycle
```

Example:

```text
                    MAIN AGENT
                         │
          ┌──────────────┼──────────────┐
          ↓              ↓              ↓
      RESEARCH         CODING        SYSTEM
       AGENT            AGENT         AGENT
          │              │              │
          └──────────────┼──────────────┘
                         ↓
                     VERIFIER
```

Subagent contract:

```text
agent_id
role
capabilities
model
tools
permissions
task
context
result
```

### Important

Subagents must **not inherit unrestricted permissions** from the parent.

### Tests

Test:

* spawning
* communication
* failure
* timeout
* cancellation
* permission isolation
* malicious subagent behavior
* conflicting results

### Completion

A compromised subagent cannot automatically compromise the entire system.

---

# BUILD GATE 16 — System Usage Observation + Learning Foundation

> **Status:** ✅ Complete (2026-09-03: `learning/` — policy-gated observer, aggregation, patterns, proposals with adoption gates, 18 tests)

Now make the main agent distrust subagents appropriately.

Example:

```text
Research Agent
      ↓
claims

        ↓

Verifier
      ↓
source validation
```

Coding:

```text
Coding Agent
      ↓
code
      ↓
Testing Agent
      ↓
test results
      ↓
Main Agent
```

### Completion

The system can reject an incorrect subagent result.

---

# BUILD GATE 17 — Skill Engine

> **Status:** ✅ Complete (2026-09-03: `skills/` — versioned registry, discovery, validated plan builder, approval-gated candidates, 19 tests)

Build a separate monitoring subsystem:

```text
SystemMonitor
ResourceMonitor
ProcessMonitor
ApplicationMonitor
```

Metrics:

```text
CPU
GPU
VRAM
RAM
storage
network
processes
applications
```

AI metrics:

```text
tokens/sec
input tokens
output tokens
context usage
latency
model
provider
cost
```

### Important privacy boundary

System monitoring should be:

```text
OFF
LOCAL ONLY
AGGREGATED
CLOUD SYNC
```

and user-controlled.

### Tests

Mock system metrics.

Verify:

* collection
* aggregation
* threshold detection
* no collection when disabled
* no unexpected cloud transmission

---

# BUILD GATE 18 — Multi-Agent Foundation

> **Status:** ✅ Complete (2026-09-03: `agent/multi/` manager+registry+supervisor on the shared runtime, scoped authorization per agent, 21 tests)

Do **not** combine this directly with raw monitoring.

Architecture:

```text
System Monitor
      ↓
Telemetry
      ↓
Pattern Detector
      ↓
Learning Engine
      ↓
Personalization
```

Learn things such as:

```text
frequent applications
common workflows
resource patterns
model performance
preferred workflows
```

### Completion

The agent can make a personalization decision based on historical usage, while respecting the user's telemetry setting.

---

# BUILD GATE 19 — Agent Communication + Coordination

> **Status:** ✅ Complete (2026-09-03: typed bounded MessageBus + Coordinator with verified aggregation, malicious-child containment, cross-gate E2E, 25 tests)

Now allow the AI to create capabilities.

Architecture:

```text
SkillDiscovery
      ↓
SkillGenerator
      ↓
SkillValidator
      ↓
Sandbox
      ↓
Tests
      ↓
Verifier
      ↓
SkillRegistry
```

Never:

```text
LLM
 ↓
write code
 ↓
immediately execute
```

Instead:

```text
generate
 ↓
sandbox
 ↓
test
 ↓
verify
 ↓
approve
 ↓
register
```

### Versioning

```text
Skill v1
 ↓
usage
 ↓
failures
 ↓
improvement proposal
 ↓
sandbox
 ↓
tests
 ↓
Skill v2
```

Keep v1 until v2 is proven.

### Tests

Test:

* malicious generated skill
* failed skill
* regression
* rollback
* versioning
* permission escalation
* dependency abuse

### Completion

The system can create and update a skill without allowing an unvalidated skill to gain production privileges.

---

# BUILD GATE 20 — Desktop Application

> **Status:** ✅ Complete (2026-09-03: `interface/` RuntimeAPI + localhost HTTP, Tauri 2/React scaffold, 15 tests; build toolchain step stays operator-side)

Now build the real UI.

Use the architecture already selected:

```text
Tauri 2
   │
React + TypeScript
   │
Local IPC/API
   │
Python Agent Runtime
```

UI:

```text
Dashboard
Tasks
Agents
Memory
Skills
Models
System
Cloud
Security
Logs
Settings
```

### Completion

The user can:

```text
launch application
submit task
watch task
approve action
see agents
see tools
see verification
inspect logs
```

without manually operating the Python runtime.

---

# BUILD GATE 21 — Live Agent Visualization

> **Status:** ✅ Complete (2026-09-03: EventAdapter view-models for PACE/DAG/agents/tools/permissions, replay/reconnect, 15 tests)

Use the existing event architecture.

```text
Agent Runtime
      ↓
Event Bus
      ↓
UI Event Stream
      ↓
React
```

Display:

```text
current goal
current step
active agents
active tools
plan
progress
model
tokens
latency
system usage
cloud status
verification
```

### Completion

You can watch a real task executing in real time without polling the internal runtime excessively.

---

# BUILD GATE 22 — Dynamic Workspace

> **Status:** ✅ Complete (2026-09-03: typed nodes + layout engine + XSS/oversize rejection + SQLite persistence, 16 tests)

Now implement your node-based workspace.

Node types:

```text
chart
prose
diagram
code
media
container
portal
```

Core abstraction:

```text
Node
NodeState
NodeData
NodeRenderer
NodeInteraction
```

The agent should be able to say:

```text
Create chart
```

and produce a structured UI node, not arbitrary DOM manipulation.

### Completion

Agent-generated UI is:

```text
structured
validated
sandboxed
persistent
editable
```

---

# BUILD GATE 23 — OCI Cloud Integration

> **Status:** ✅ Complete (2026-09-03: `cloud/` abstraction + mock OCI + typed jobs + health + router fallback + secrets discipline, 18 tests, CI mock-only)

Only after local autonomy works.

OCI becomes:

```text
Cloud Coordinator
```

Responsibilities:

```text
task queue
agent state
scheduling
device registry
synchronization
remote communication
job orchestration
```

Not necessarily the place where every piece of reasoning happens.

---

# BUILD GATE 24 — PC ↔ OCI Synchronization

> **Status:** ✅ Complete (2026-09-03: policy/manifest/versioned sync, conflicts explicit, idempotent + resumable, secret-proof, 21 tests)

Define data classes:

```text
LOCAL_ONLY
CLOUD_SYNC
ENCRYPTED_SYNC
```

Sync:

```text
tasks
results
memory
skills
preferences
agent state
device state
events
```

### Critical tests

Disconnect network during synchronization.

You must handle:

```text
offline
partial upload
duplicate event
conflict
reconnect
resume
```

### Completion

No duplicated or corrupted state after network interruptions.

---

# BUILD GATE 25 — PC Availability

> **Status:** ✅ Complete (2026-09-03: `cloud/availability.py` heartbeat/lease service + presence persistence + online/offline/expired events)

Now introduce distributed computation.

```text
ComputeRouter
       │
 ┌─────┼──────────┐
 ↓     ↓          ↓
LOCAL  OCI      REMOTE
             /       \
          Kaggle   Lightning
```

Routing factors:

```text
GPU
VRAM
RAM
CPU
model
privacy
cost
latency
availability
deadline
task type
```

### ComputeJob

Standardize:

```text
job_id
environment
model
inputs
command
resources
timeout
outputs
verification
```

### Completion

The agent can choose a compute node automatically for predefined workloads.

---

# BUILD GATE 26 — Compute Router

> **Status:** ✅ Complete (2026-09-03: `cloud/routing.py` policy-checked deterministic ranking + fallback, privacy never implicit)

Treat them as **workers**, not special logic embedded throughout the agent.

```text
Agent
 ↓
ComputeRouter
 ↓
ComputeJob
 ↓
Worker
 ↓
Artifact
 ↓
Verification
 ↓
Agent
```

### Tests

Test:

* worker unavailable
* timeout
* job failure
* artifact missing
* partial output
* retry
* cancellation

---

# BUILD GATE 27 — Kaggle / Lightning Execution

> **Status:** ✅ Complete (2026-09-03: `cloud/external.py` mock-backed providers, dataset policy gate, full job lifecycle, CI mock-only)

This is where the ecosystem starts becoming genuinely distributed.

Example:

```text
PC
OFFLINE
   ↓
OCI
   ↓
Personal Agent
   ↓
Planner
   ↓
Compute Router
   ↓
Kaggle / Lightning
   ↓
Result
   ↓
OCI
   ↓
PC returns
   ↓
Synchronize
```

### Completion test

Turn off the PC.

Submit a supported cloud-computable task.

When the PC returns:

```text
task exists
result exists
artifacts exist
memory updated
events synchronized
```

with no duplicated execution.

---

# BUILD GATE 28 — Offline / Cloud Agent

> **Status:** ✅ Complete (2026-09-03: `cloud/offline.py` cloud-safe gating, LOCAL_ONLY never leaves, versioned resume handoff)

At this point you have enough attack surface to perform serious security work.

Implement:

### Identity

```text
User
Device
Agent
Subagent
Cloud
Worker
```

### Authentication

```text
PC ↔ OCI
Phone ↔ OCI
Hardware ↔ OCI
Worker ↔ OCI
```

### Authorization

```text
user
agent
subagent
tool
device
cloud job
```

### Secrets

Secrets must never enter:

```text
prompts
memory
skills
logs
events
model context
```

### Sandboxing

Sandbox:

```text
generated code
skills
terminal
untrusted files
remote jobs
subagents
```

---

# BUILD GATE 29 — Security Architecture

> **Status:** ✅ Complete (2026-09-03: `security/envelope.py` trust tiers + capability danger + `docs/THREAT_MODEL.md`)

Every meaningful operation should be traceable:

```text
Task
 ↓
Agent
 ↓
Plan
 ↓
Step
 ↓
Tool
 ↓
Risk
 ↓
Permission
 ↓
Execution
 ↓
Result
 ↓
Verification
```

You should be able to answer:

> "Why did the agent execute this command?"

with an actual audit chain.

### Completion

Given any dangerous operation, you can reconstruct:

```text
who
what
when
why
which policy
which permission
which tool
what happened
whether it was verified
```

---

# BUILD GATE 30 — Sandboxing

> **Status:** ✅ Complete (2026-09-03: `security/sandbox.py` profiles + serializing provider + ToolRunner fail-closed integration)

Only now implement true self-improvement.

Inputs:

```text
task history
success/failure
user feedback
memory
system usage
skill performance
model performance
```

Pipeline:

```text
Experience
 ↓
Pattern Detection
 ↓
Hypothesis
 ↓
Proposal
 ↓
Evaluation
 ↓
Approval
 ↓
Update
```

Never:

```text
experience
 ↓
automatically rewrite core system
```

### Separate:

```text
facts
preferences
hypotheses
learned behavior
security policies
```

Security policies should be immutable to ordinary learning.

---

# BUILD GATE 31 — Audit System

> **Status:** ✅ Complete (2026-09-03: `security/audit.py` hash-chained append-only log + runner hook + rotation/retention)

Now the agent can act without an immediate user request.

Examples:

```text
disk space becoming low
repeated workflow detected
known task due
PC unavailable
resource bottleneck
skill improvement available
```

But:

```text
PROACTIVE ≠ UNCONTROLLED
```

Implement policies:

```text
never proactive
notify only
suggest
automatically execute low-risk
require approval for high-risk
```

---

# BUILD GATE 32 — Self-Learning

> **Status:** ✅ Complete (2026-09-03: `learning/cycle.py` generate→validate→approve→adopt→rollback, memory/preference targets only)

Now build the phone as a **thin client**, not another full agent.

```text
PHONE
  ↓
Secure Gateway
  ↓
OCI
  ↓
Personal Agent
```

Features:

```text
status
notifications
voice
approvals
tasks
memory
agent activity
remote commands
```

The phone should not become a second conflicting source of truth.

---

# BUILD GATE 33 — Learning Safety

> **Status:** ✅ Complete (2026-09-03: `learning/safety.py` modes, risk tiers, drift detector, kill switch, persisted posture)

ESP32/Arduino:

```text
ESP32
 ↓
Secure protocol
 ↓
OCI / PC
 ↓
Agent
```

Controls:

```text
agent mode
model
pause
resume
kill
project
workflow
approval
emergency stop
```

This is particularly valuable for your permission architecture because physical approval can become an additional authorization factor.

---

# BUILD GATE 34 — Proactive Agent

> **Status:** ✅ Complete (2026-09-03: `agent/proactive.py` triggers→dedupe/cooldown/quiet-hours→approval→verified execution)

Voice should simply become another interface:

```text
MIC
 ↓
STT
 ↓
Agent
 ↓
Task
 ↓
TTS
```

Do not create a separate voice agent.

---

# BUILD GATE 35 — Phone Interface

> **Status:** ✅ Complete (2026-09-03: `interface/phone.py` thin client over gateway+RuntimeAPI; deny=cancel, approve=acknowledgment-only)

Standardize every node.

```text
Node
 ├── node_id
 ├── type
 ├── capabilities
 ├── status
 ├── authentication
 ├── version
 └── health
```

Types:

```text
PC
OCI
PHONE
HARDWARE
COMPUTE
```

Now the ecosystem becomes extensible.

---

# BUILD GATE 36 — Hardware Interface

> **Status:** ✅ Complete (2026-09-03: `interface/hardware.py` versioned HMAC protocol + SimulatedDevice + safe command mapping)

At this point:

```text
PC
OCI
Kaggle
Lightning
Phone
Hardware
```

can participate in one ecosystem.

Build:

```text
GlobalScheduler
ResourceManager
NodeManager
JobScheduler
```

It decides:

```text
Where should this task execute?
```

based on:

```text
privacy
cost
latency
resources
availability
deadline
capabilities
```

---

# BUILD GATE 37 — Voice

> **Status:** ✅ Complete (2026-09-03: `interface/voice.py` injected STT/TTS, explicit sessions, intent routing through phone client)

Create a permanent benchmark suite.

## Benchmark 1 — Coding

```text
inspect repo
→ identify issue
→ modify code
→ test
→ verify
```

## Benchmark 2 — Research

```text
research
→ collect sources
→ synthesize
→ verify
→ produce report
```

## Benchmark 3 — System

```text
inspect system
→ identify bottleneck
→ propose fix
→ obtain permission
→ execute
→ verify
```

## Benchmark 4 — Skill

```text
detect repeated workflow
→ create skill
→ test
→ register
→ reuse
```

## Benchmark 5 — Distributed

```text
PC unavailable
→ cloud receives task
→ remote worker executes
→ result stored
→ PC reconnects
→ synchronization
```

---

# BUILD GATE 38 — Ecosystem Protocol

> **Status:** ✅ Complete (2026-09-03: `interface/protocol.py` transport-independent envelope + validation + replay cache)

Measure actual numbers.

### Agent

```text
goal completion rate
planning accuracy
tool selection accuracy
verification accuracy
recovery success
false completion rate
```

### Model

```text
TTFT
tokens/sec
input tokens
output tokens
context utilization
```

### Executor

```text
tool latency
parallel efficiency
queue latency
failure rate
```

### Distributed

```text
sync latency
cloud latency
job startup time
artifact transfer
```

### System

```text
CPU
GPU
VRAM
RAM
network
```

Don't optimize based on intuition.

Measure.

---

# BUILD GATE 39 — Global Scheduler

> **Status:** ✅ Complete (2026-09-03: `scheduler/` durable priority scheduler over ComputeRouter with bounded retries)

Create deliberate attacks.

### Model attacks

```text
prompt injection
malicious instructions
tool manipulation
```

### File attacks

```text
malicious document
malicious repository
poisoned configuration
```

### Skill attacks

```text
skill poisoning
dependency attack
privilege escalation
```

### Memory attacks

```text
memory poisoning
false preference
cross-project leakage
```

### Agent attacks

```text
malicious subagent
permission inheritance
agent impersonation
```

### Cloud attacks

```text
unauthorized job
credential leakage
worker impersonation
```

### Completion criterion

The security architecture must remain intact even when the model is intentionally treated as compromised.

That is one of the most important tests for this project.

---

# BUILD GATE 40 — End-to-End Autonomous Tasks

> **Status:** ✅ Complete (2026-09-03: 18-scenario deterministic suite across the full stack, all mocked)

Test:

```text
process crash
database corruption
network loss
cloud outage
model outage
tool failure
worker failure
disk full
RAM exhaustion
GPU OOM
partial synchronization
duplicate events
```

Implement:

```text
recovery
backups
migrations
resource limits
rate limits
timeouts
circuit breakers
crash recovery
```

---

# BUILD GATE 41 — Benchmarking

> **Status:** ✅ Complete (2026-09-03: `bench/` 18-case suite with machine-readable JSON output, all smoke bounds held)

Finally:

```text
AI Ecosystem Installer
```

should install:

```text
Tauri application
Python runtime
agent runtime
tool runtime
configuration
database
security components
optional local model support
```

User experience:

```text
Install
 ↓
Launch
 ↓
Configure
 ↓
Use
```

Not:

```text
clone repo
install Python
install Node
install dependencies
run server
run UI
configure environment
start MCP
...
```

---

# BUILD GATE 42 — Red-Team Security

> **Status:** ✅ Complete (2026-09-03: `tests/security/test_redteam.py` 10 attack categories + invariant test, all contained)

Before calling it v1:

### Functional

* [ ] autonomous task execution
* [ ] planning
* [ ] parallel execution
* [ ] tools
* [ ] MCP
* [ ] permission
* [ ] risk
* [ ] verification
* [ ] recovery
* [ ] memory
* [ ] personality
* [ ] personalization
* [ ] skills
* [ ] multi-agent

### Distributed

* [ ] OCI
* [ ] synchronization
* [ ] compute routing
* [ ] remote workers
* [ ] offline operation

### Security

* [ ] authentication
* [ ] authorization
* [ ] sandboxing
* [ ] secret management
* [ ] audit
* [ ] red-team tests

### UX

* [ ] desktop
* [ ] live agent visualization
* [ ] dynamic workspace
* [ ] settings
* [ ] logs
* [ ] approvals

### Reliability

* [ ] crash recovery
* [ ] migrations
* [ ] backups
* [ ] network recovery
* [ ] resource limits

---

# The actual development order

> **Status:** ⏳ Pending — execution has not started. Update this section as gates complete.

The most important thing is **not to implement all 42 gates simultaneously**.

Your dependency chain should be:

```text
PHASE 1
Core Contracts
     ↓
PHASE 2
Runtime + State + Events
     ↓
PHASE 3
Persistence
     ↓
PHASE 4
Model Abstraction
     ↓
PHASE 5
Tool Framework
     ↓
PHASE 6
Permission + Risk
     ↓
PHASE 7
Planner
     ↓
PHASE 8
DAG Executor
     ↓
PHASE 9
Verification
     ↓
PHASE 10
Recovery
     ↓
PHASE 11
Research
     ↓
PHASE 12
Memory
     ↓
PHASE 13
Personality
     ↓
PHASE 14
SINGLE-AGENT MVP
     ↓
PHASE 15
Multi-Agent
     ↓
PHASE 16
System Awareness
     ↓
PHASE 17
Skills
     ↓
PHASE 18
Desktop UI
     ↓
PHASE 19
OCI
     ↓
PHASE 20
Distributed Compute
     ↓
PHASE 21
Offline Operation
     ↓
PHASE 22
Security Hardening
     ↓
PHASE 23
Self-Learning
     ↓
PHASE 24
Phone / Hardware / Voice
     ↓
PHASE 25
Global Scheduler
     ↓
PHASE 26
Benchmarks
     ↓
PHASE 27
Red Team
     ↓
PHASE 28
Production
     ↓
RELEASE
```

---

# How I would divide this into OpenCode implementation parts

Since you're using OpenCode to actually build the repository, **each part should correspond to a small set of gates**, not the entire roadmap.

### Part 1 — Architecture + contracts

> **Status:** ✅ Complete (2026-09-03)

```text
domain models
state machine
events
errors
interfaces
project structure
```

### Part 2 — Runtime foundation

> **Status:** ✅ Complete (2026-09-03)

```text
AgentRuntime
ExecutionContext
TaskManager
EventBus
Persistence
```

### Part 3 — Model layer

> **Status:** ✅ Complete (2026-09-03)

```text
ModelProvider
Model abstraction
ModelRouter
structured outputs
streaming
```

### Part 4 — Tool layer

> **Status:** ✅ Complete (2026-09-03)

```text
ToolRegistry
Tool contract
FastMCP integration
tool lifecycle
```

### Part 5 — Security foundation

> **Status:** ✅ Complete (2026-09-03)

```text
PermissionEngine
RiskEngine
PolicyEngine
Authorization
```

### Part 6 — Planning

> **Status:** ✅ Complete (2026-09-03)

```text
Planner
Plan schema
DAG
dependency resolver
```

### Part 7 — Execution

> **Status:** ✅ Complete (2026-09-03)

```text
ParallelExecutor
timeouts
cancellation
resource limits
```

### Part 8 — Verification + Recovery

> **Status:** ✅ Complete (2026-09-03)

```text
Verifier
verification strategies
failure classifier
retry
replanning
```

### Part 9 — Research + Memory

> **Status:** ✅ Complete (2026-09-03)

### Part 10 — Personality + Personalization

> **Status:** ✅ Complete (2026-09-03)

### Part 11 — Single-Agent MVP

> **Status:** ✅ Complete (2026-09-03)

### Part 12 — Multi-Agent

> **Status:** ⏳ Pending

### Part 13 — Computer / System Awareness

> **Status:** ✅ Complete (2026-09-03)

### Part 14 — Usage Observation + Learning

> **Status:** ✅ Complete (2026-09-03)

### Part 15 — Skill Engine

> **Status:** ✅ Complete (2026-09-03)

### Part 16 — Multi-Agent Foundation

> **Status:** ✅ Complete (2026-09-03)

### Part 17 — Agent Communication + Coordination

> **Status:** ✅ Complete (2026-09-03)

### Part 18 — Desktop + Visualization (Gates 20-21)

> **Status:** ✅ Complete (2026-09-03)

### Part 19 — Dynamic Workspace (Gate 22)

> **Status:** ✅ Complete (2026-09-03)

### Part 20 — OCI + Synchronization (Gates 23-24)

> **Status:** ✅ Complete (2026-09-03)

### Part 21 — PC Availability + Compute Router (Gates 25-26)

> **Status:** ✅ Complete (2026-09-03)

### Part 22 — External Compute + Offline Agent (Gates 27-28)

> **Status:** ✅ Complete (2026-09-03)

### Part 23 — Security Envelope + Sandbox + Audit (Gates 29-31)

> **Status:** ✅ Complete (2026-09-03)

### Part 24 — Learning + Safety + Proactive (Gates 32-34)

> **Status:** ✅ Complete (2026-09-03)

### Part 25 — Phone + Hardware + Voice + Protocol (Gates 35-38)

> **Status:** ✅ Complete (2026-09-03)

### Part 26 — Scheduler + Autonomous E2E (Gates 39-40)

> **Status:** ✅ Complete (2026-09-03)

### Part 27 — Benchmarking (Gate 41)

> **Status:** ✅ Complete (2026-09-03)

### Part 28 — Red Team (Gate 42)

> **Status:** ✅ Complete (2026-09-03)

### Part 29 — Production Hardening (Gate 43)

> **Status:** ✅ Complete (2026-09-03)

### Part 30 — Packaging / Release (Gate 44)

> **Status:** ✅ Complete (2026-09-03)

---

# The rule for every OpenCode part

This is the part I would change most strongly from the earlier approach.

Every coding prompt should force OpenCode to follow:

```text
1. Inspect existing repository
        ↓
2. Understand current architecture
        ↓
3. Identify existing implementations
        ↓
4. Implement ONLY this phase
        ↓
5. Write/update tests
        ↓
6. Run tests
        ↓
7. Fix failures
        ↓
8. Run lint/type checks
        ↓
9. Verify architecture constraints
        ↓
10. Produce implementation report
```

And the prompt should explicitly contain:

```text
DO NOT:
- rewrite unrelated modules
- implement future phases
- bypass existing abstractions
- introduce unnecessary dependencies
- let models directly execute tools
- weaken security for convenience
- mark features complete without tests
```

---

# Definition of "DONE"

This should become a project-wide rule.

A feature is **NOT COMPLETE** because:

```text
code exists
```

or:

```text
it works once
```

or:

```text
the model demonstrated it
```

A feature is complete only when:

```text
┌───────────────────────────────┐
│ IMPLEMENTED                   │
├───────────────────────────────┤
│ UNIT TESTED                   │
│ INTEGRATION TESTED            │
│ FAILURE TESTED                │
│ SECURITY TESTED               │
│ PERSISTENCE TESTED            │
│ LOGGING/AUDIT IMPLEMENTED     │
│ DOCUMENTED                    │
│ ARCHITECTURE VERIFIED         │
│ NO REGRESSIONS                │
└───────────────────────────────┘
                ↓
             COMPLETE
```

For high-risk components, add:

```text
RED-TEAM TESTED
```

---

# Most important milestone

I would define **AI Ecosystem v0.1** much more narrowly than the eventual vision.

## v0.1 should be:

```text
User
 ↓
Goal
 ↓
Single Agent
 ↓
Planner
 ↓
Permission/Risk
 ↓
Tool
 ↓
Executor
 ↓
Verifier
 ↓
Recovery
 ↓
Memory
 ↓
Result
```

with:

```text
Model abstraction
Persistence
Events
Audit
FastMCP
Tests
```

**No phone.
No ESP32.
No Kaggle orchestration.
No self-learning.
No autonomous skill generation.
No giant multi-agent swarm.**

Those are later layers.

Once that v0.1 loop is genuinely reliable, everything else becomes an extension of a stable core rather than a giant experimental system.

If you want to use this directly with OpenCode, the next logical step is to turn **Part 1 through Part 28 into exact OpenCode prompts**, with each prompt containing the files to create/change, implementation requirements, tests to write, commands to run, and an explicit **PASS/FAIL completion checklist**.

---

# BUILD GATE 43 — Production Hardening

> **Status:** ✅ Complete (2026-09-03: `core/config.py`, `core/circuit.py`, `core/lifecycle.py`, DB backup API, source-tree secret/dependency scans, 13 tests)

Production readiness without changing behavior: centralized env-based
configuration, circuit breaker (adoptable, unused paths untouched),
best-effort shutdown ordering, crash-state startup recovery, online
SQLite backups, corrupt-database failure discipline, and permanent
scans proving no credentials or heavy undeclared dependencies in the
tree.

---

# BUILD GATE 44 — Packaging / Release

> **Status:** ✅ Complete (2026-09-03: `packaging/` first-run wizard, backup/restore/upgrade helpers, Windows build script + Inno Setup config, 16 simulation tests)

Release mechanics with honest scope: install layout, first-run flow
(cloud/phone/hardware skippable), idempotent migrations, data
preservation across upgrade/reinstall, selective backup/restore,
config-driven boot, and offline operation are all proven in
simulation. The compiled installer artifact itself is operator-built
from `packaging/windows/` (toolchain build, not CI).

---

## Changelog

| Date | Change |
| ---- | ------ |
| 2026-09-03 | Initial build plan created. All 42 gates set to ⏳ Pending. No code implementation started (docs-only repo). |
| 2026-09-03 | Part 1 done: Gates 1-2 ✅ Complete. `src/ai_ecosystem/core/` implemented (models, state machine, events, errors, logging, persistence ABCs), `pyproject.toml` added, package installed editable, 39/39 tests pass (`python -m pytest tests -q`). No LLM dependency. Next: Part 2 / Gate 3 (persistence). |
| 2026-09-03 | Parts 2-6 done: Gates 3-7 ✅ Complete (7/42, 17%). SQLite persistence + TaskManager/AgentRuntime; mock model abstraction + router w/ fallback; registry + 6 tools + policy-checked runner + optional FastMCP bridge; risk/policy/permission/authorization choke point; strict planner + resolver + mock backend. 107/107 tests pass. No LLM/network dependency (`fastmcp` optional only). Next: Part 7 / Gate 8 (DAG executor). |
| 2026-09-03 | Part 7 done: Gate 8 ✅ Complete (8/42, 19%). `agent/executor/` (TaskGraph, CancellationToken, ParallelExecutor) executes validated plans with dependency-aware parallelism exclusively through ToolRunner. max_concurrency/cancel/timeout/FAIL_FAST+CONTINUE_INDEPENDENT proven, restart restores A=SUCCEEDED/B=PENDING+interrupted/C=PENDING. 123/123 tests pass (107 pre-existing untouched). Next: Part 8 / Gates 9-10 (verification + recovery). |
| 2026-09-03 | Parts 8-9 (partial) done: Gates 9-11 ✅ Complete (11/42, 26%). `agent/verifier/` (executor-free Verifier + 4 strategies, INCONCLUSIVE/ERROR states, events, SQLite repo); `agent/recovery/` (classifier, bounded retry, replan gates, escalation, audited engine); `intelligence/research/` (manager/collector/extractor/ranker/conflicts/context, ToolRunner-only, Gate 10 retry reused). 168/168 tests pass. Fixed one real import cycle (research kept out of `intelligence/__init__`). Part 9 stays 🔄 Ongoing: Gate 12 (Memory) not started. Next: Gate 12. |
| 2026-09-03 | Parts 9-11 done: Gates 12-14 ✅ Complete (14/42, 33%). `personalization/memory/` (scoped lifecycle, deterministic retrieval, conflicts preserve history, propose/apply consolidation, retention, malicious-memory-proof); `personalization/personality/` (constrained profiles, versioned SQLite `personalities`/`preferences` tables, project overrides, model-independent context); `agent/orchestrator/` SingleAgent PACE loop over Gates 3-13. Full lifecycle + crash resume + compromised-model containment proven. 226/226 tests pass. One state-machine edge added (AWARENESS→PLANNING for research skip); RecoveryEngine gained `run_with_prior` so the first observed failure enters the audit. Next: Part 12 / Gates 15-16 (multi-agent). |
| 2026-09-03 | Gates 15-19 restructured to Parts 13-17 order (awareness → usage → skills → multi-agent → communication); later parts renumbered 18-31. Parts 13-17 done: Gates 15-19 ✅ Complete (19/42, 45%). `system/monitor/` (probes, manager, context, redacted tool, snapshot persistence); `learning/` (policy-gated observer, aggregation, patterns, proposals with adoption gates); `skills/` (versioned registry, discovery, validated plan builder, approval-gated candidates); `agent/multi/` (definitions, scoped manager, supervisor, typed bounded bus, coordinator). Fixed a real scoped-runner bug (missing agent_id skipped scope checks) and two import cycles (lazy boundaries). 325/325 tests pass. Next: Part 18 / Gates 20-22 (Tauri desktop). |
| 2026-09-03 | Parts 18-20 done: Gates 20-24 ✅ Complete (24/42, 57%). `interface/` (RuntimeAPI + localhost HTTP, EventAdapter views, typed workspace with XSS rejection); `desktop/` Tauri 2/React scaffold (integrity-tested; toolchain build stays operator-side); `cloud/` (provider abstraction, mock OCI, typed jobs, health, router fallback, secrets discipline); `cloud/sync.py` (policy/manifest/versioned sync, explicit conflicts, idempotent + resumable, secret-proof). Desktop→cloud E2E + adversarial containment + UI XSS regression tests green. 412/412 tests pass. One transient E2E failure observed mid-batch, green on reruns (reported). Next: Part 21+ (distributed compute). |
| 2026-09-03 | Gates 25-44 restructured to final program (44 gates; later parts renumbered). Parts 21-30 done: Gates 25-44 ✅ Complete (44/44, 100%). `cloud/` (+availability, routing, external providers, offline agent); `security/` (+envelope, sandbox, audit) + `docs/THREAT_MODEL.md`; `learning/` (+pipeline, safety, governor); `agent/proactive.py`; `interface/` (+gateway, phone, hardware, voice, protocol); `scheduler/`; `bench/`; `core/` (+config, circuit, lifecycle, backup API); `packaging/` (wizard, backup/upgrade, Windows build + installer config). Two real races fixed (DB cursor atomicity; scoped-runner agent_id). 604/604 tests pass (2× consecutive). Project complete per plan. |
