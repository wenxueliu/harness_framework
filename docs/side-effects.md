# Side Effects, Idempotency, and Compensation

Tasks that mutate external state declare:

```json
{
  "side_effecting": true,
  "idempotency_scope": "deployment",
  "compensation_task": "rollback"
}
```

The compensation task must declare `activation: compensation_only`. Aggregator
never activates such a task through normal DAG progression; only a failed
side-effect record can move it from `BLOCKED` to `PENDING`.

Before calling an external system, a worker invokes `side_effect.py begin` with
the business idempotency key and current attempt ownership. The ledger returns:

- `EXECUTE` for a newly acquired key.
- `RESUME` when the same attempt repeats its begin call.
- `REPLAY` with the stored result after successful completion.
- `WAIT_FOR_COMPENSATION` after failure.
- `COMPENSATED` after rollback has completed.

Completion records the structured external result. Failure durably records the
error and atomically claims the compensation task status with CAS. The
compensation worker calls `side_effect.py compensated --source-task ...` after
undoing the external change. Both source and compensation writes are fenced by
their immutable attempt ID and lease epoch.

Idempotency keys are SHA-256-addressed in KV while the original key remains in
the collision-checked record. A key owned by another in-progress attempt cannot
be stolen, preventing watchdog retries from blindly repeating an uncertain
external mutation.
