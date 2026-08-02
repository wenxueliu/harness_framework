# ChangeSet Lifecycle

A `ChangeSet` is the audited unit for changing an existing workflow. It records
the proposed resource edits, the exact base version IDs used by the proposer,
impact-analysis results, decisions, actors, timestamps, and reasons.

The enforced lifecycle is:

```text
PROPOSED → IMPACT_ANALYZED → APPROVED → APPLIED
    └──────────→ REJECTED
    └──────────→ SUPERSEDED
```

Analyzed or approved changes may also be rejected or superseded where allowed.
`APPLIED`, `REJECTED`, and `SUPERSEDED` are terminal and cannot be reopened.
Approval cannot skip impact analysis.

`ChangeSetStore` updates the current record with CAS and appends an immutable
history event after every successful transition. Concurrent decision attempts
raise `ChangeSetConflict`, requiring the caller to reload the latest decision.
The `base_versions` map lets the apply phase reject proposals calculated from
stale Requirement, WorkflowSpec, DAG, or Plan revisions.
