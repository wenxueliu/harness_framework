# Evaluator Loop Policies

`evaluator_policy` bounds an execution-layer Generator–Verifier loop without
putting repository-specific verification commands into Harness. The worker runs
its own evaluator, then reports one normalized score and verdict per iteration.
Harness deterministically returns `PASS`, `RETRY`, `SWITCH_FALLBACK`, or
`ESCALATE` and stores the full decision history.

## Task configuration

```json
{
  "backend": {
    "type": "backend",
    "service_name": "users",
    "depends_on": [],
    "evaluator_policy": {
      "max_iterations": 3,
      "plateau_window": 2,
      "plateau_delta": 0.01,
      "fallback_chain": ["primary", "narrowed", "degraded"],
      "escalation_target": "human"
    }
  }
}
```

- `max_iterations` is the hard limit for each strategy.
- `plateau_window` is the number of recent scores used for plateau detection.
- `plateau_delta` is the maximum score range considered a plateau.
- `fallback_chain` contains ordered, unique strategy names.
- `escalation_target` identifies the human queue or role receiving the issue.

Exhaustion or a plateau switches to the next fallback. Exhausting the final
strategy escalates. Defaults are three iterations, a three-score zero-delta
window, the single strategy `primary`, and escalation to `human`.

## Reporting an iteration

```bash
python skills/stage-bridge/scripts/record_evaluation.py \
  req-001 backend 0.72 FAIL \
  --details '{"gate":"unit-tests","failed":2}' \
  --attempt-id "$ATTEMPT_ID" --lease-epoch "$LEASE_EPOCH"
```

On `RETRY`, revise and evaluate again. On `SWITCH_FALLBACK`, use the returned
`state.strategy`. `PASS` and `ESCALATE` are terminal. Escalation creates a
durable record on the task and under
`workflows/<req_id>/human_interventions/`.

Scores need only be comparable within a task; higher-is-better is recommended.
A `PASS` verdict ends the loop regardless of score. Completion contracts remain
authoritative, so required gate evidence must still be recorded before task
completion. Writes are attempt-fenced and state updates use CAS to reject stale
workers and concurrent observations.
