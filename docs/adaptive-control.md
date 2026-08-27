# Evidence-driven adaptive control

Adaptive control adds recoverable, interruptible execution to Harness DAGs.
The dependency graph remains acyclic and owns scheduling. Dynamic routing
creates a successor run and invalidates a DAG closure; it never adds a back
edge to the graph.

## Core model

Task execution and conclusion validity are independent:

| Concern | Values |
|---|---|
| execution | `BLOCKED`, `PENDING`, `IN_PROGRESS`, `WAITING_FOR_HUMAN`, `DONE`, `FAILED`, ... |
| validity | `UNKNOWN`, `VALID`, `STALE`, `INVALIDATED` |

New tasks start with `UNKNOWN` validity. Normal completion sets `VALID`.
Requirement changes and recovery routes archive outputs, set the affected
downstream closure to `INVALIDATED`, and schedule only that closure.

## Atomic action protocol

1. `GET .../adaptive/next` issues one action bound to an attempt and state version.
2. The Agent executes only that action.
3. `POST .../adaptive/check` submits fresh evidence and consumes the action.
4. The next request returns a route boundary.
5. `POST .../adaptive/route` submits the earliest invalid target and evidence.

Action issuance and consumption use CAS. Stale attempts, action identifiers,
and state versions are rejected.

### Issue or resume an action

```text
GET /api/workflow/REQ/task/TASK/adaptive/next?actor=AGENT&attempt_id=ATTEMPT&type=EXECUTE
```

### Submit check evidence

```json
POST /api/workflow/REQ/task/TASK/adaptive/check
{
  "action_id": "act-...",
  "state_version": 4,
  "verdict": "PASS",
  "verifier": "pytest",
  "actor": "agent-1",
  "workspace_revision": "commit-sha",
  "evidence": {"tests": 12, "failed": 0},
  "command": {
    "argv": ["python", "-m", "pytest", "tests/test_api.py"],
    "cwd": ".",
    "exit_code": 0,
    "output_digest": "sha256:..."
  }
}
```

### Route after a check

```json
POST /api/workflow/REQ/task/test/adaptive/route
{
  "target_task": "implementation",
  "reason": "the failure exposes an implementation defect",
  "evidence": "TC-12 expected PAID, observed PENDING",
  "still_valid": ["requirements", "design"],
  "invalidated": ["implementation", "test"],
  "failure_fingerprint": "order-state-TC-12",
  "actor": "agent-1"
}
```

Harness verifies that the target is an already visited ancestor and that
`invalidated` exactly equals its downstream closure. Uncompensated
side-effecting tasks are not legal recovery targets.

A passed check also exposes `__complete__`. Completion requires an empty
`invalidated` list, the current task in `still_valid`, and every existing
CompletionContract artifact and gate to be satisfied.

## Routing budgets

`workflows/<req>/routing/budget` may override the default policy:

```json
{
  "policy": {
    "max_total_routes": 8,
    "max_same_edge_routes": 2,
    "max_same_failure_fingerprint": 2
  },
  "state": {"total": 0, "edges": {}, "fingerprints": {}}
}
```

Exhaustion moves the source task to `WAITING_FOR_HUMAN` instead of continuing
an unbounded reaction loop.

## Human feedback

Human messages are separate from test-repair messages:

```text
DELIVERED -> OBSERVED -> ACKNOWLEDGED -> APPLIED
```

`APPLIED` means the message affected execution; it does not replace task
verification. An Agent responds with `CONTINUE`, `ASK`, or `PAUSE`. `ASK`
opens a structured question and puts the task into `WAITING_FOR_HUMAN`.

```text
POST .../adaptive/feedback
POST .../adaptive/respond
POST .../adaptive/answer
GET  .../adaptive/feedback
GET  .../adaptive/boundary
```

Boundary priority is:

```text
ABORT > PAUSE > AWAIT_HUMAN > FEEDBACK > ROUTE > ACTIVE
```

The platform-neutral `skills/stage-bridge/scripts/adaptive_boundary.py`
adapter can be called by host user-message, pre-tool, and post-tool hooks.
Exit code 6 means a non-abort boundary blocks business tools; exit code 7
means abort. Control operations may pass `--control-operation`.

## Assessed requirement changes

```json
POST /api/workflow/REQ/requirement-change/assessed
{
  "content": "new requirement text",
  "reason": "contract changed",
  "still_valid": ["design"],
  "invalidated": ["backend", "test"],
  "evidence": "API response contract changed",
  "actor": "alice"
}
```

Harness derives minimal changed roots, verifies the declared downstream
closure, publishes the version, rolls the run forward, and records a
`GOAL_REVISED` audit event.

## Audit events

Adaptive events live below `workflows/<req>/events/`. Every event carries the
actor, timestamp, run/task identity, causation ID, correlation ID, and payload.
Events cover actions, checks, routes, invalidations, feedback, questions,
controls, and goal revisions.
