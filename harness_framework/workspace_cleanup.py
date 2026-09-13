"""Background Run Workspace retention worker.

The worker deliberately only advances states through WorkspaceManager's guarded
methods.  RETAINED workspaces are never touched automatically; CLEANUP_PENDING
workspaces are moved to Trash and Trash entries are purged after the grace
window.  This keeps cleanup recoverable and makes the policy testable through
the same API used by operators.
"""
from __future__ import annotations

import datetime
import json
import logging
import threading
from typing import Any

from .workspace_manager import WorkspaceManager
from .event_journal import EventJournal

log = logging.getLogger("workspace-cleanup")


class WorkspaceCleanupWorker:
    def __init__(
        self, manager: WorkspaceManager, *, interval: float = 60.0,
        grace_hours: int = 24, event_journal: EventJournal | None = None,
    ):
        self.manager = manager
        self.interval = max(1.0, float(interval))
        self.grace_hours = max(1, int(grace_hours))
        self.event_journal = event_journal or EventJournal(manager.store)
        self._stop = threading.Event()
        self.last_run: str | None = None
        self.last_result: dict[str, Any] = {}

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        log.info("workspace cleanup worker started (interval=%ss)", self.interval)
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                log.exception("workspace cleanup tick failed")
            self._stop.wait(self.interval)

    def run_once(self, *, now: datetime.datetime | None = None) -> dict[str, Any]:
        current = now or datetime.datetime.now(datetime.timezone.utc)
        result = {"trashed": [], "purged": [], "skipped": [], "errors": []}
        items: list[dict[str, Any]] = []
        cursor = None
        while True:
            page, cursor = self.manager.store.kv_list("workflows/", cursor=cursor, limit=1000)
            items.extend(page)
            if cursor is None:
                break
        seen: set[tuple[str, str]] = set()
        for item in items:
            key = item.get("key", "")
            if not key.endswith("/workspace/record"):
                continue
            parts = key.split("/")
            if len(parts) < 5:
                continue
            req_id, run_id = parts[1], parts[3]
            if (req_id, run_id) in seen:
                continue
            seen.add((req_id, run_id))
            try:
                workspace = json.loads(item.get("value", "{}"))
                status = workspace.get("status")
                if status == "CLEANUP_PENDING":
                    moved = self.manager.trash_run_workspace(req_id, run_id)
                    result["trashed"].append(moved["run_workspace_id"])
                    self._event(req_id, run_id, moved, "TRASHED")
                elif status == "TRASHED":
                    metadata_key = f"workflows/{req_id}/runs/{run_id}/workspace/retention/trashed_at"
                    trashed_at, _ = self.manager.store.kv_get(metadata_key)
                    if not trashed_at:
                        result["skipped"].append(run_id)
                        continue
                    timestamp = datetime.datetime.fromisoformat(
                        trashed_at.replace("Z", "+00:00")
                    )
                    if current - timestamp >= datetime.timedelta(hours=self.grace_hours):
                        deleted = self.manager.purge_run_workspace(
                            req_id, run_id, now=current
                        )
                        result["purged"].append(deleted["run_workspace_id"])
                        self._event(req_id, run_id, deleted, "DELETED")
            except Exception as exc:  # one bad workspace must not stop cleanup
                log.warning("workspace cleanup failed for %s/%s: %s", req_id, run_id, exc)
                result["errors"].append({"req_id": req_id, "run_id": run_id, "error": str(exc)})
        self.last_run = current.isoformat().replace("+00:00", "Z")
        self.last_result = result
        return result

    def _event(self, req_id: str, run_id: str, workspace: dict[str, Any], status: str) -> None:
        self.event_journal.append(
            "WORKSPACE_STATUS_CHANGED",
            subject={"req_id": req_id, "run_id": run_id,
                     "workspace_id": workspace.get("run_workspace_id", "")},
            actor={"type": "system", "id": "workspace-cleanup"},
            data={"status": status},
        )
