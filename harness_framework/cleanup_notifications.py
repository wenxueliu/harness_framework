"""Administrator-visible cleanup notifications backed by KV and EventJournal."""
from __future__ import annotations

import uuid
import json
from typing import Any

from .event_journal import EventJournal
from .kv_store_protocol import KVStore


class CleanupNotificationService:
    PREFIX = "notifications/cleanup"

    def __init__(self, store: KVStore, event_journal: EventJournal | None = None):
        self.store = store
        self.event_journal = event_journal or EventJournal(store)

    def resolve_group(self, req_id: str, run_id: str,
                      workspace: dict[str, Any] | None = None) -> str:
        group_id, _ = self.store.kv_get(f"workflows/{req_id}/project-group")
        if not group_id and isinstance(workspace, dict):
            project_id = workspace.get("project_workspace_id", "")
            if project_id:
                raw, _ = self.store.kv_get(
                    f"workspaces/projects/{project_id}/record"
                )
                try:
                    group_id = str(json.loads(raw).get("group_id", ""))
                except (TypeError, ValueError, json.JSONDecodeError):
                    group_id = ""
        return str(group_id or "unassigned")

    def notify(self, *, group_id: str, resource_type: str, action: str,
               req_id: str, run_id: str, details: dict[str, Any] | None = None) -> dict:
        notification_id = f"clean-{uuid.uuid4().hex[:12]}"
        record = {
            "notification_id": notification_id,
            "group_id": group_id,
            "resource_type": resource_type,
            "action": action,
            "req_id": req_id,
            "run_id": run_id,
            "details": details or {},
            "audience": "PROJECT_GROUP_ADMIN",
            "status": "SENT",
        }
        self.store.kv_put(f"{self.PREFIX}/{group_id}/{notification_id}", _json(record))
        self.event_journal.append(
            "CLEANUP_ADMIN_NOTIFIED",
            subject={"group_id": group_id, "req_id": req_id, "run_id": run_id},
            actor={"type": "system", "id": "cleanup-worker"},
            data={**record, "notification_id": notification_id},
        )
        return record

    def list_for_group(self, group_id: str) -> list[dict]:
        items: list[dict] = []
        cursor = None
        while True:
            page, cursor = self.store.kv_list(
                f"{self.PREFIX}/{group_id}/", cursor=cursor, limit=1000
            )
            for item in page:
                try:
                    items.append(_loads(item.get("value", "{}")))
                except (TypeError, ValueError):
                    continue
            if cursor is None:
                break
        return sorted(items, key=lambda item: str(item.get("notification_id", "")))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: Any) -> dict:
    return json.loads(value)
