# Execution Model

## Canonical execution lifecycle

```text
Goal
 -> understand/context
 -> structured plan
 -> validate plan
 -> build TaskGraph
 -> capability/risk analysis
 -> policy/authorization
 -> approval if required
 -> reserve durable action identity
 -> execute via ToolRunner
 -> observe external state
 -> verify
 -> recover/replan/escalate if needed
 -> persist/audit/memory
```

## Plan contract

A plan is a typed set of steps. Each step has an identity, description, dependencies, tool requirements and completion/verification information. The validator rejects malformed plans, unknown tools and invalid dependencies before graph execution.

Never convert free-form model text directly into a shell command and execute it.

## DAG execution

`TaskGraph` represents dependencies. `ParallelExecutor` submits only ready work and can execute independent nodes concurrently. The executor delegates actual tool invocation to `ToolRunner`.

Failure policy can stop dependent/unstarted work while allowing explicitly independent work to continue when configured. State transitions must represent what actually happened.

## Step deadlines

Each step can have a timeout/deadline represented through a child cancellation token. The token can be shared with cooperative handlers and sandbox workers. A deadline does not prove termination for arbitrary in-process Python code.

## Durable action identity

An action is semantically identified from stable task/tool/argument information. The purpose is to prevent replay and duplicate side effects across retries/restarts. Model-generated IDs may be useful correlation IDs but must not be the sole idempotency key.

## Tool execution

Registered tools expose metadata and schemas. The runner is the mandatory choke point for authorization and execution. Filesystem/git operations apply root confinement; terminal operations have stricter execution policy and network defaults.

## Verification

Verification occurs after execution and evaluates observable facts. Appropriate strategies can inspect artifacts, filesystem state, command/test results, or other domain-specific evidence. The verifier can return a negative or inconclusive result; the orchestrator must not turn uncertainty into `COMPLETED`.

## Recovery

Failures are classified before retry/replan. Retries are bounded and should not automatically repeat permission/security failures. If execution may still be active, recovery must account for that uncertainty before starting conflicting work.

## Crash/restart behavior

Persisted execution context, graph state and action records allow the runtime to reconstruct what was known before a crash. On restart, unknown/in-flight operations require reconciliation; the system must not assume that process death means the external side effect was rolled back.

## Execution state truthfulness

Use these distinctions consistently:

- completed: desired operation executed and accepted by verification
- failed: operation completed/terminated with a known failure
- cancelled: cancellation requested and work did not proceed, or the execution boundary can prove termination
- timed out: deadline exceeded; do not imply underlying work was killed unless proven
- inconclusive: available evidence cannot establish success/failure
- recovering: runtime is actively reconciling/retrying/replanning

## Adding a new executable operation

Implement it as a registered capability, define its input/output contract, assign risk/capabilities, route it through policy/authorization and `ToolRunner`, establish an execution boundary, add verification, add adversarial tests, and document the trust assumptions.
