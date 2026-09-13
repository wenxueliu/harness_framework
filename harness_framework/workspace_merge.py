"""Explicit Attempt-to-Attempt merge tasks.

Merges are never implicit when an isolated Binding is used. The API creates a
durable Merge Task and an operator explicitly applies it after reviewing the
bounded patch. Git's three-way apply keeps conflicts visible.
"""
from __future__ import annotations

import datetime
import json
import subprocess
import uuid
from typing import Any

from .api_errors import ConflictError, NotFoundError, ValidationError
from .event_journal import EventJournal
from .workspace_manager import WorkspaceManager


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


class WorkspaceMergeService:
    def __init__(self, store, manager: WorkspaceManager, journal: EventJournal | None = None):
        self.store = store
        self.manager = manager
        self.journal = journal or EventJournal(store)

    def _task_key(self, req_id: str, run_id: str, merge_id: str) -> str:
        return f"workflows/{req_id}/runs/{run_id}/merge-tasks/{merge_id}"

    def _load(self, req_id: str, run_id: str, merge_id: str) -> dict[str, Any]:
        raw, _ = self.store.kv_get(self._task_key(req_id, run_id, merge_id))
        if not raw:
            raise NotFoundError("Merge Task 不存在", code="MERGE_TASK_NOT_FOUND")
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ConflictError("Merge Task 记录损坏", code="MERGE_TASK_CORRUPT") from exc

    def create_task(
        self, *, req_id: str, run_id: str, source_task_id: str,
        source_attempt_id: str, target_task_id: str, target_attempt_id: str,
        actor: str, message: str = "",
    ) -> dict[str, Any]:
        source = self.manager.get_attempt_binding(req_id, run_id, source_task_id, source_attempt_id)
        target = self.manager.get_attempt_binding(req_id, run_id, target_task_id, target_attempt_id)
        if source["binding_id"] == target["binding_id"]:
            raise ValidationError("源和目标 Attempt 不能相同", code="MERGE_SAME_BINDING")
        if not target.get("writable"):
            raise ValidationError("目标 Attempt Workspace 只读", code="MERGE_TARGET_READ_ONLY")
        merge_id = f"merge_{uuid.uuid4().hex}"
        record = {
            "merge_id": merge_id, "req_id": req_id, "run_id": run_id,
            "source_task_id": source_task_id, "source_attempt_id": source_attempt_id,
            "target_task_id": target_task_id, "target_attempt_id": target_attempt_id,
            "source_binding_id": source["binding_id"], "target_binding_id": target["binding_id"],
            "status": "PENDING", "message": str(message or ""),
            "created_by": actor, "created_at": _now_iso(), "revision": 1,
        }
        if not self.store.kv_put(self._task_key(req_id, run_id, merge_id), json.dumps(record), cas=0):
            raise ConflictError("Merge Task ID 冲突", code="MERGE_TASK_CONFLICT")
        self.journal.append(
            "MERGE_TASK_CREATED",
            subject={"req_id": req_id, "run_id": run_id,
                     "task_id": target_task_id, "attempt_id": target_attempt_id},
            actor={"type": "human", "id": actor},
            data={"merge_id": merge_id, "source_binding_id": source["binding_id"],
                  "target_binding_id": target["binding_id"]},
        )
        return record

    def _patch(self, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", source, "diff", "--binary", "HEAD", "--"],
            capture_output=True, text=True, timeout=60, check=False,
        )

    def preview(self, req_id: str, run_id: str, merge_id: str) -> dict[str, Any]:
        task = self._load(req_id, run_id, merge_id)
        source = self.manager.resolve_attempt_path(req_id, run_id, task["source_task_id"], task["source_attempt_id"])
        target = self.manager.resolve_attempt_path(req_id, run_id, task["target_task_id"], task["target_attempt_id"])
        process = self._patch(source)
        if process.returncode not in {0, 1}:
            raise ValidationError("源 Workspace 不是可合并的 Git 工作区", code="MERGE_SOURCE_NOT_GIT")
        return {**task, "diff": process.stdout[-1024 * 1024:],
                "diff_truncated": len(process.stdout) > 1024 * 1024,
                "source_path": source, "target_path": target}

    def apply(self, req_id: str, run_id: str, merge_id: str, *, actor: str) -> dict[str, Any]:
        task = self._load(req_id, run_id, merge_id)
        if task.get("status") == "DONE":
            return task
        if task.get("status") not in {"PENDING", "CONFLICT"}:
            raise ConflictError("Merge Task 当前状态不可执行", code="MERGE_TASK_NOT_EXECUTABLE")
        source = self.manager.resolve_attempt_path(req_id, run_id, task["source_task_id"], task["source_attempt_id"])
        target = self.manager.resolve_attempt_path(req_id, run_id, task["target_task_id"], task["target_attempt_id"])
        source_patch = self._patch(source)
        if source_patch.returncode not in {0, 1}:
            raise ValidationError("源 Workspace 不是可合并的 Git 工作区", code="MERGE_SOURCE_NOT_GIT")
        patch = source_patch.stdout
        if not patch.strip():
            task.update({"status": "DONE", "finished_at": _now_iso(), "applied_by": actor, "message": task.get("message") or "无差异"})
            self.store.kv_put(self._task_key(req_id, run_id, merge_id), json.dumps(task))
            return task
        applied = subprocess.run(
            ["git", "-C", target, "apply", "--3way", "--index", "-"],
            input=patch, capture_output=True, text=True, timeout=60, check=False,
        )
        if applied.returncode != 0:
            task.update({"status": "CONFLICT", "updated_at": _now_iso(), "error": applied.stderr[-4000:]})
            self.store.kv_put(self._task_key(req_id, run_id, merge_id), json.dumps(task))
            raise ConflictError("Merge Task 发生冲突，请人工解决", code="MERGE_CONFLICT",
                                details={"merge_id": merge_id, "stderr": applied.stderr[-1000:]})
        task.update({"status": "DONE", "finished_at": _now_iso(), "applied_by": actor, "error": ""})
        self.store.kv_put(self._task_key(req_id, run_id, merge_id), json.dumps(task))
        self.journal.append(
            "MERGE_TASK_APPLIED",
            subject={"req_id": req_id, "run_id": run_id,
                     "task_id": task["target_task_id"], "attempt_id": task["target_attempt_id"]},
            actor={"type": "human", "id": actor}, data={"merge_id": merge_id},
        )
        return task
