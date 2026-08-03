"""Apply a requirement revision inside an existing workflow."""
from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

from .incremental import affected_downstream_closure, invalidate_impacted_tasks
from .kv_store_protocol import KVStore
from .run_manager import RunManager
from .versioning import VersionedResourceStore


class RequirementChangeService:
    """Publish a new requirement revision and rerun only affected tasks."""

    def __init__(self, store: KVStore):
        self.store = store
        self.versions = VersionedResourceStore(store)
        self.runs = RunManager(store)

    def apply(
        self,
        req_id: str,
        *,
        content: str,
        reason: str,
        changed_tasks: list[str],
        actor: str,
    ) -> dict[str, Any]:
        req_id = _required(req_id, "req_id")
        content = _required(content, "content")
        reason = _required(reason, "reason")
        actor = _required(actor, "actor")
        if not isinstance(changed_tasks, list) or not all(
            isinstance(task, str) and task.strip() for task in changed_tasks
        ):
            raise ValueError("changed_tasks must contain non-empty strings")
        changed_tasks = sorted(set(changed_tasks))

        current_requirement, current_version = self.versions.get_current(
            req_id, "requirement"
        )
        if current_version is None:
            raise ValueError(f"workflow has no versioned requirement: {req_id}")
        dag = self._load_dag(req_id)
        affected = sorted(affected_downstream_closure(dag, changed_tasks))

        lock_key = f"workflows/{req_id}/requirement_change_lock"
        lock_id = uuid.uuid4().hex
        if not self.store.kv_put(lock_key, lock_id, cas=0):
            raise RuntimeError("another requirement change is in progress")

        change_id = f"reqchg-{uuid.uuid4().hex}"
        record_key = (
            f"workflows/{req_id}/requirement_changes/{change_id}/record"
        )
        old_run_id, _ = self.store.kv_get(f"workflows/{req_id}/current_run")
        record = {
            "change_id": change_id,
            "req_id": req_id,
            "status": "APPLYING",
            "reason": reason,
            "actor": actor,
            "changed_at": _now_iso(),
            "from_revision": current_version.revision,
            "to_revision": None,
            "changed_tasks": changed_tasks,
            "affected_tasks": affected,
            "old_run_id": old_run_id or "",
            "new_run_id": old_run_id or "",
        }
        self.store.kv_put(record_key, _json(record))

        try:
            document = (
                dict(current_requirement)
                if isinstance(current_requirement, dict)
                else {"previous_content": current_requirement}
            )
            document.update({
                "req_id": req_id,
                "content": content,
                "reason": reason,
                "change_id": change_id,
                "changed_by": actor,
                "changed_at": record["changed_at"],
            })
            new_version = self.versions.publish(
                req_id, "requirement", document, actor=actor,
                expected_revision=current_version.revision,
            )
            self.store.kv_put(f"workflows/{req_id}/requirement", content)
            self.store.kv_put(
                f"workflows/{req_id}/requirement_version",
                str(new_version.revision),
            )

            new_run_id = old_run_id or ""
            if affected:
                invalidate_impacted_tasks(
                    self.store, req_id, dag, changed_tasks,
                    change_id=change_id,
                )
                if old_run_id:
                    new_run_id = self.runs.roll_forward_run(
                        req_id, actor=actor, change_id=change_id,
                        affected_tasks=affected,
                    )
                else:
                    new_run_id = self.runs.get_or_create_run(req_id, actor=actor)

            record.update({
                "status": "APPLIED",
                "to_revision": new_version.revision,
                "requirement_version_id": new_version.version_id,
                "new_run_id": new_run_id,
                "applied_at": _now_iso(),
            })
            self.store.kv_put(record_key, _json(record))
            self.store.kv_put(
                f"workflows/{req_id}/requirement_changes/current", change_id
            )
            return dict(record)
        except Exception as exc:
            record.update({
                "status": "FAILED", "error": str(exc),
                "failed_at": _now_iso(),
            })
            self.store.kv_put(record_key, _json(record))
            raise
        finally:
            current_lock, _ = self.store.kv_get(lock_key)
            if current_lock == lock_id:
                self.store.kv_delete(lock_key)

    def _load_dag(self, req_id: str) -> dict[str, dict[str, Any]]:
        dag, _version = self.versions.get_current(req_id, "dag")
        if dag is None:
            raw, _ = self.store.kv_get(f"workflows/{req_id}/dependencies")
            if not raw:
                raise ValueError(f"workflow has no DAG: {req_id}")
            try:
                dag = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("workflow dependencies are invalid JSON") from exc
        if not isinstance(dag, dict):
            raise ValueError("workflow DAG must be an object")
        return dag


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
