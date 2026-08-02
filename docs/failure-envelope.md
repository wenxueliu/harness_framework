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
