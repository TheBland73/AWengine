# Design notes

This document records the engineering rationale behind AWEngine: the boundaries
between components, the guarantees each layer provides, and the trade-offs that
shape the current release. It is intended for maintainers and for users who need
to reason about failure behavior rather than just author a workflow.

## 1. Goals

AWEngine is built around four properties:

1. **Auditable control flow.** Branching, loops, retries, and stopping rules live
   in a declarative workflow file. The engine does not contain business-specific
   agent names or prompts.
2. **Deterministic acceptance.** A run is successful only when the configured
   verification command passes on a disposable copy of the workspace. Model
   output is evidence, not proof.
3. **Crash safety.** Every state transition is checkpointed atomically. A run can
   be inspected after the fact and resumed after a process failure.
4. **Reproducibility.** Recorded provider responses allow the full demo and test
   suite to run offline and produce stable results.

Non-goals for this release are multi-process scheduling, container isolation,
and a benchmark/evaluation framework. A thin interactive shell wraps the normal
CLI; it does not introduce a second orchestration path.

## 2. Layers

```text
CLI (+ interactive shell adapter) / library API
        |
        v
loader + spec + expression evaluator
        |
        v
engine (routing, retries, rounds, convergence)
        |
        +--> node runtimes ---> LLM provider / tools
        |                  ---> sandbox executor
        |
        +--> state + checkpoint + lock
        +--> event bus + trace artifacts
```

The dependency direction is enforced by `tests/test_layering.py`. Lower layers
never import the engine or CLI, and node implementations never import engine
internals. This keeps workflow execution testable in isolation and makes the
node protocol portable.

## 3. Control plane

A workflow is a directed graph of nodes. Edges carry optional expressions; the
first edge whose condition evaluates to true is taken. `END` terminates the
graph. Back edges are detected with a depth-first traversal and define round
boundaries: a round starts at the entry node and ends when an executed edge
points back to an ancestor. This is what allows a forward edge such as
`apply_fix -> review_final` to run before the next round begins.

Retries are per node. A failed handler is retried up to `retry` times; a contract
violation (missing or mistyped output fields) is handled the same way. When the
retries are exhausted, the node result is recorded as a failure and routing
continues through failure-aware edges.

Convergence is evaluated at the end of a round. Limits are checked before
success so that a goal reached after exceeding a budget is not reported as a
clean success. The evaluation order is: cheat guard, hard limits, progress
stall, success, no-op, blocked.

## 4. Verification

Verification runs in a disposable copy of the workspace, never in the user's
directory. The executor injects the holdout into `tests_holdout/` only at
verification time. Agents therefore cannot read or edit the hidden tests.

Levels 1–5 are policy labels. The engine always enforces the verification
command; a configured holdout runs at level 2 or higher; `red_before` is honored
whenever it is set. Levels 4–5 do not add hidden automatic checks — they signal
that a stricter external process (for example human review) is expected.

| Level | Engine behavior |
|---|---|
| 1 | Verification command must exit 0 |
| 2–3 | Command plus configured holdout; level 3 warns when `red_before` is absent |
| 4–5 | Same deterministic checks; reports also surface agent review verdicts |

`red_before` runs the command on the untouched copy and requires it to fail. If
the command already passes before any change, the gate cannot distinguish a real
fix from a no-op; the run is classified `verify_weak`. A visible pass combined
with a holdout failure is classified `F-VERIFY-FALSEPASS`.

## 5. State and recovery

`RunState` is the single persisted source of truth. It contains the runtime
counters, artifacts, messages, history, outputs, verification result, and a
cursor pointing at the next node.

Writes use a temporary file, `fsync`, rotation of the previous checkpoint to
`.prev`, and an atomic replace. If the primary file is corrupt, the loader falls
back to `.prev` and records a recovery warning. A lease file with a heartbeat
prevents two processes from writing the same run; stale leases can be taken over
and the takeover is recorded as an event.

The event log is append-only and is never used to reconstruct control flow. It
is an observability stream, not a write-ahead log. Reports are generated from
the checkpoint plus events, so they can be regenerated without executing the
workflow again.

## 6. Collaboration model

Agents exchange immutable artifacts and explicit messages. An artifact has an
id, type, producer, round, references, and a SHA-256 digest over its content.
Messages record source, destination, intent, and the artifacts being handed
over. This makes a multi-agent run inspectable without relying on a shared
mutable context window.

Context assembly is deterministic: sources are selected, observations are
masked and summarized, and older history is compacted in a fixed order. If the
budget still cannot be met, the run fails with `F-CONTEXT-OVERFLOW` rather than
silently truncating critical instructions.

## 7. Extension points

- `NodeHandler`: a Python callable receiving `NodeContext` and returning
  `NodeResult`.
- Process protocol: JSON on stdin/stdout, suitable for non-Python runtimes.
- Inline agents: system prompt, task, tools, normalization, and artifact
  declarations in YAML.
- `LLMProvider`: any OpenAI-compatible endpoint, plus fake and recording
  providers for offline work.
- Pricing table: per-model input, cached-input, and output prices.

## 8. Trade-offs

- **Directory copy instead of containers.** This keeps the runtime dependency
  small and works on Windows without a daemon, but it is not a security boundary.
  Untrusted code must not be executed as a process node.
- **One process per run.** Correctness is prioritized over throughput; the lease
  prevents concurrent writers. Horizontal scheduling is left to an external
  orchestrator.
- **Byte-exact cassettes.** Cassettes make tests deterministic but must be
  re-recorded whenever prompts change. The recording script is intentionally
  separate from the test suite.
- **No automatic provider price updates.** A static pricing table is predictable
  and auditable; operators are expected to update it when providers change
  prices.

## 9. Compatibility policy

The workflow schema has an explicit `version` field and the state file has a
`schema_version`. Additive fields are introduced with defaults; breaking changes
require a schema migration. The CLI follows semantic versioning and keeps exit
codes stable.
