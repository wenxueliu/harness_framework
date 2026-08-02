# Context and Memory Model

Harness separates runtime context by trust, mutability, ownership, and intended
consumer. Agent-internal model memory remains outside the framework, while
cross-task information uses the following explicit namespaces.

| Namespace | Semantics | Mutation rule | Default visibility |
|---|---|---|---|
| `facts` | Accepted immutable facts | create-only CAS | shared |
| `artifacts` | Full task outputs | immutable versions + CAS current pointer | shared |
| `working_memory/<task>` | Scratch state for one task | mutable, task-scoped | owning task only |
| `events/<task>` | Execution observations | append-only | audit APIs only |
| `summaries` | Derived context | replaceable, requires source references | shared |
| `restricted` | Confidential or secret data | classified writes | explicit authorization only |

All paths live below `workflows/<req_id>/knowledge/`. Legacy
`workflows/<req_id>/context/` keys remain readable by explicit key during
migration, but new writes must use a typed namespace.

## Integrity boundaries

- Facts cannot be overwritten. Corrections require a new fact key or a
  versioned artifact explaining the supersession.
- Artifact versions contain checksum, lineage, actor, and timestamp metadata.
  Concurrent publication uses CAS and cannot overwrite another candidate.
- Working memory is keyed by task and the generic reader rejects access to a
  different task's memory.
- Events receive unique sequence keys and are never returned by the generic
  context reader.
- Summaries must identify their source records, so derived text never becomes
  indistinguishable from an original fact.
- Restricted values require `CONFIDENTIAL` or `SECRET` classification and are
  denied by default.

## Agent commands

`read_context.py <req_id>` returns only facts, artifacts, and summaries. An
explicit namespaced key can select one record; restricted data and audit events
cannot be accessed through this generic command.

`write_artifact.py --scope context` publishes a version under
`knowledge/artifacts` and requires the current task's `attempt_id` and
`lease_epoch`. Stale workers therefore cannot replace shared output.

Task-to-task commands and notifications continue to use `message_bus.py`; event
logs describe what happened, while messages coordinate future work.

## Declared task inputs

Each executable task declares `context_inputs` in the workflow specification:

```json
{
  "context_inputs": [
    "facts/coding-standards",
    "artifacts/api-spec",
    "summaries/product-requirement"
  ]
}
```

Claim paths resolve exactly these selectors. Artifact selectors resolve through
their current version pointer to the full immutable value. An absent declaration
injects an empty context; it never means “all workflow context.” Prefix selectors
ending in `/*` are supported for bounded namespaces. Generic task inputs cannot
select restricted data, event logs, or another task's working memory. Legacy
keys require an explicit `legacy/<key>` selector during migration.

## Checkpoint and resume

Long-running workers can persist a checkpoint with:

```bash
python skills/stage-bridge/scripts/write_checkpoint.py \
  "$REQ_ID" "$TASK_NAME" "batch:10" '{"offset":10}' \
  --attempt-id "$ATTEMPT_ID" --lease-epoch "$LEASE_EPOCH"
```

Each checkpoint has an immutable payload and a manifest containing producer
attempt, lease epoch, cursor, checksum, artifact references, and timestamp.
Writes are attempt-fenced and the current-version pointer uses CAS. Every claim
path verifies and returns the latest checkpoint as `resume_checkpoint`; the
persistent worker injects it as `_resume_checkpoint`. A recovered attempt can
resume durable state while stale workers remain fenced.

## Resource budgets

Tasks may declare `resource_budget` limits for tokens, USD cost, tool calls, and
wall-clock seconds. Workers report deltas through `record_usage.py`; the ledger
uses attempt fencing and CAS accumulation. Crossing any configured limit opens
a durable circuit breaker with the exceeded dimensions and usage snapshot.
While the breaker is open, completion contracts reject `DONE` even when no
artifact gates were configured. Operators must inspect the usage and explicitly
decide whether to revise the budget, retry with a narrowed strategy, or abort.
