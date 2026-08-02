# Harness Production Hardening TODO

This document is the execution ledger for hardening `harness_framework` and
its `multiagents` integration. Items are ordered by dependency and production
risk. A checked item requires implementation, tests, and documentation.

## P0 — Scheduling correctness

- [x] Replace the current parallel-node "activation equals completion" behavior
  with explicit fork/join semantics.
- [x] Add join policies for `all`, `any`, and `quorum`, including deterministic
  partial-failure handling.
- [x] Propagate terminal upstream failures to downstream tasks using an explicit
  `SKIPPED_UPSTREAM_FAILED` terminal state.
- [x] Ensure a run always reaches a terminal state when no runnable work remains.
- [x] Add orchestration-invariant tests for fork/join, failure propagation, and
  stale in-memory snapshots.

## P0 — Dynamic DAG consistency

- [x] Make dynamic task addition update the authoritative `dependencies` DAG
  and task metadata as one conflict-detected operation.
- [x] Validate missing dependencies, cycles, terminal dependencies, and task-name
  collisions before publishing a new DAG revision.
- [x] Make Proposal state freeze scheduling, claiming, and watchdog recovery for
  the affected scope.
- [x] Preserve rejected proposals separately instead of silently deleting history.

## P0 — Attempt ownership and leases

- [x] Generate an immutable `attempt_id` and monotonic `lease_epoch` at claim.
- [x] Require the current attempt and lease epoch for artifact writes,
  completion, and failure reporting.
- [x] Add renewable leases and distinguish soft timeout from hard timeout.
- [x] Fence stale workers after watchdog recovery so late writes cannot overwrite
  the current attempt.

## P1 — Contracts, artifacts, and verification

- [x] Add `AgentContract` fields for inputs, outputs, responsibilities,
  exclusions, permissions, and context budget.
- [x] Add versioned Artifact Manifests with producer attempt, checksum, lineage,
  validation status, and retention metadata.
- [x] Add structured verifier evidence and completion contracts.
- [x] Prevent `DONE` unless required artifacts and gates are satisfied.
- [x] Add evaluator-loop policies: maximum iterations, score plateau detection,
  fallback chain, and escalation.

## P1 — Requirement changes and incremental delivery

- [x] Version Requirement, WorkflowSpec, DAG, and Plan independently.
- [x] Add ChangeSet lifecycle: proposed, impact-analyzed, approved, applied,
  rejected, superseded.
- [x] Compute affected downstream closure and invalidate only impacted artifacts,
  evidence, tasks, and attempts.
- [x] Support safe roll-forward of an active run and preserve the previous run as
  `SUPERSEDED`.

## P1 — Context and long-running work

- [x] Separate immutable facts, versioned artifacts, task working memory, event
  logs, derived summaries, and restricted data.
- [x] Declare task `context_inputs`; stop injecting the entire workflow context.
- [x] Store full artifacts plus bounded summaries with mandatory preserved fields.
- [x] Add checkpoint manifests and resume-from-checkpoint retry behavior.
- [x] Add token, cost, tool-call, and wall-clock budgets with circuit breakers.

## P1 — Failure and recovery policy

- [x] Add a structured Failure Envelope covering hard, silent, partial,
  contradiction, cascade, loop, and context failures.
- [ ] Add idempotency keys and compensation tasks for side-effecting work.
- [ ] Define primary, narrowed fallback, degraded, and human escalation paths.

## P2 — Observability, security, and integration

- [ ] Model requirement, run, attempt, agent call, and tool call as trace/span
  relationships.
- [ ] Record model, latency, tokens, cost, confidence, and structured outcome.
- [ ] Enforce per-role least-privilege tool and data access.
- [ ] Generate `multiagents` requirement tracker views from Harness runtime state,
  removing the current dual-source-of-truth behavior.
- [ ] Add migration and backward-compatibility documentation.

## Release gates

- [ ] Unit tests pass without collection errors.
- [ ] DAG property tests cover cycles, joins, failures, retries, and revisions.
- [ ] Fault-injection tests cover worker death, stale completion, duplicate claim,
  restart, and partial fan-out failure.
- [ ] E2E tests cover requirement change during execution and checkpoint recovery.
- [ ] Documentation and examples use one canonical status and schema vocabulary.
