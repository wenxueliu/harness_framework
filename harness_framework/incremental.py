"""Impact closure and selective invalidation for incremental delivery."""
from __future__ import annotations

import json
from typing import Any, Iterable

from .kv_store_protocol import KVStore


ATTEMPT_FIELDS = (
    "attempt_id", "assigned_agent", "lease_expires_at", "lease_renewed_at",
    "hard_deadline_at", "started_at",
)


def affected_downstream_closure(
    dependencies: dict[str, dict[str, Any]], changed_tasks: Iterable[str]
) -> set[str]:
    """Return changed tasks and every transitively dependent downstream task."""
    changed = set(changed_tasks)
    unknown = changed - set(dependencies)
    if unknown:
        raise ValueError("unknown changed tasks: " + ", ".join(sorted(unknown)))
    reverse: dict[str, set[str]] = {name: set() for name in dependencies}
    for task, definition in dependencies.items():
        for raw in definition.get("depends_on", []):
            dependency = raw.get("task", "") if isinstance(raw, dict) else raw
            if dependency in reverse:
                reverse[dependency].add(task)
    closure = set(changed)
    queue = sorted(changed)
    while queue:
        current = queue.pop(0)
        for downstream in sorted(reverse[current]):
            if downstream not in closure:
                closure.add(downstream)
                queue.append(downstream)
    return closure


def invalidate_impacted_tasks(
    store: KVStore, req_id: str, dependencies: dict[str, dict[str, Any]],
    changed_tasks: Iterable[str], *, change_id: str,
) -> set[str]:
    """Archive and clear active outputs/ownership only for the affected closure."""
    if not change_id:
        raise ValueError("change_id is required")
    impacted = affected_downstream_closure(dependencies, changed_tasks)
    statuses = {
        task: (store.kv_get(f"workflows/{req_id}/tasks/{task}/status")[0] or "")
        for task in dependencies
    }
    for task in sorted(impacted):
        base = f"workflows/{req_id}/tasks/{task}"
        archive = f"workflows/{req_id}/invalidations/{change_id}/tasks/{task}"
        for namespace in ("artifacts", "evidence"):
            _archive_prefix(store, f"{base}/{namespace}/", f"{archive}/{namespace}/")
            store.kv_delete(f"{base}/{namespace}", recurse=True)
        for field in ATTEMPT_FIELDS:
            value, _ = store.kv_get(f"{base}/{field}")
            if value is not None:
                store.kv_put(f"{archive}/attempt/{field}", value)
                store.kv_delete(f"{base}/{field}")
        store.kv_put(f"{base}/invalidated_by", change_id)
        deps = [_dependency_name(dep) for dep in dependencies[task].get("depends_on", [])]
        ready = all(dep not in impacted and statuses.get(dep) == "DONE" for dep in deps)
        store.kv_put(f"{base}/status", "PENDING" if ready else "BLOCKED")
    store.kv_put(
        f"workflows/{req_id}/invalidations/{change_id}/affected_tasks",
        json.dumps(sorted(impacted)),
    )
    return impacted


def _archive_prefix(store: KVStore, source: str, destination: str) -> None:
    items, _ = store.kv_get(source, recurse=True)
    for item in items or []:
        relative = item["Key"][len(source):]
        store.kv_put(destination + relative, item.get("_decoded", ""))


def _dependency_name(value: Any) -> str:
    return str(value.get("task", "")) if isinstance(value, dict) else str(value)
