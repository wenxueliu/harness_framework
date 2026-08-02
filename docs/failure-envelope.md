# Failure Envelope

Every task failure now records a schema-versioned envelope in addition to the
legacy `error_message` projection. The taxonomy covers:

- `HARD`: explicit command, process, or infrastructure failure.
- `SILENT`: missing expected progress or output without an explicit error.
- `PARTIAL`: only part of a fan-out or side effect completed.
- `CONTRADICTION`: evidence or outputs disagree.
- `CASCADE`: failure caused by an upstream failure envelope.
- `LOOP`: retry, evaluator, or feedback loop exhaustion.
- `CONTEXT`: missing, corrupt, oversized, or unauthorized context.

Each envelope carries a unique ID, severity, retryability, task and immutable
attempt ownership, timestamp, structured evidence, and causal failure IDs.
`fail_task.py` accepts `--failure-type`, `--severity`, `--evidence`, and repeated
`--caused-by` arguments. The latest envelope is stored at `failure/current` and
every envelope is retained under `failure/history/<failure_id>`. Persistent
workers use the same envelope format for internal failures.

## Recovery paths

Each task may define a `recovery_policy` with four ordered paths:

1. `PRIMARY` retries the same strategy for `primary_attempts`.
2. `NARROWED` retries a smaller scope or cheaper verifier.
3. `DEGRADED` continues with an explicitly reduced service level.
4. `HUMAN` opens an intervention for `human_target`.

`select_recovery.py` reads the current Failure Envelope and retry count, selects
the path deterministically, and writes current plus append-only decision
history. Critical failures and non-retryable failures escalate immediately;
partial failures may still enter automatic recovery so their compensation path
can run. Exhausting all configured attempt budgets creates a durable human
intervention linked to the originating failure ID.
