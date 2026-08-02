# Impact Closure and Selective Invalidation

For a ChangeSet that edits one or more tasks, Harness builds a reverse DAG and
computes the deterministic closure containing each changed task and every
transitive downstream consumer. Tasks outside this set are not modified.

For each impacted task, active artifact and verifier-evidence trees are copied
to `invalidations/<change_id>/` before their active pointers are removed.
Attempt ownership and lease fields are archived and cleared, fencing the old
worker. The task becomes `PENDING` only when all of its dependencies are
unaffected and already `DONE`; otherwise it becomes `BLOCKED` for normal DAG
reactivation. The affected task list is stored with the invalidation record.

This preserves auditability while ensuring completion contracts cannot
accidentally accept stale outputs. Unaffected artifacts, evidence, attempts,
and task states retain their original keys and values.

## Active-run roll-forward

After approved versions are published and impacted tasks are invalidated,
`RunManager.roll_forward_run` initializes a successor with the ChangeSet ID,
affected tasks, and an exact snapshot of all four current resource versions.
A short CAS lock serializes roll-forward operations. The new run is initialized
before the `current_run` pointer moves; the old run is then finalized as
`SUPERSEDED` with a backlink to its successor. The previous run, transitions,
sessions, and artifacts remain queryable.
