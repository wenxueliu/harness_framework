"""Artifact data model with retention policy."""
from __future__ import annotations

import json
import time
import logging
import threading
from typing import Any

from .kv_store_protocol import KVStore
from .cleanup_notifications import CleanupNotificationService

def _now_iso(): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

log = logging.getLogger("artifact-cleanup")

class ArtifactError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code = code; self.message = message; self.status = status
        super().__init__(f"{code}: {message}")

class ArtifactService:
    PREFIX = "artifacts"

    def __init__(self, store: KVStore, notification_service=None):
        self.store = store
        self.notification_service = notification_service or CleanupNotificationService(store)

    def _read(self, key: str, default=None):
        raw, _ = self.store.kv_get(key)
        return json.loads(raw) if raw else default

    def _write(self, key: str, value):
        self.store.kv_put(key, json.dumps(value, ensure_ascii=False))

    def create(self, body: dict) -> dict:
        artifact_id = body.get("artifact_id") or f"art-{int(time.time())}"
        if not body.get("instance_id", "").strip():
            raise ArtifactError("INSTANCE_REQUIRED", "instance_id 不能为空")
        if not body.get("path", "").strip():
            raise ArtifactError("PATH_REQUIRED", "path 不能为空")
        retention_days = body.get("retention_days", 7)
        created_at = _now_iso()
        record = {
            "artifact_id": artifact_id,
            "attempt_id": body.get("attempt_id", ""),
            "instance_id": body["instance_id"],
            "type": body.get("type", "log"),
            "path": body["path"],
            "size_bytes": body.get("size_bytes", 0),
            "sha256": body.get("sha256", ""),
            "mime_type": body.get("mime_type"),
            "created_at": created_at,
            "retention_until": self._compute_retention(created_at, retention_days),
            "status": "ACTIVE",
        }
        self._write(f"{self.PREFIX}/{body['instance_id']}/{artifact_id}", record)
        return record

    @staticmethod
    def _compute_retention(created_at: str, days: int) -> str:
        import datetime
        dt = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        expiry = dt + datetime.timedelta(days=days)
        return expiry.strftime("%Y-%m-%dT%H:%M:%SZ")

    def get(self, instance_id: str, artifact_id: str) -> dict:
        record = self._read(f"{self.PREFIX}/{instance_id}/{artifact_id}")
        if not record:
            raise ArtifactError("ARTIFACT_NOT_FOUND", f"Artifact {artifact_id} 不存在", 404)
        return record

    def list_for_instance(self, instance_id: str) -> list[dict]:
        results = []
        cursor = None
        while True:
            page, cursor = self.store.kv_list(f"{self.PREFIX}/{instance_id}/", cursor=cursor, limit=1000)
            for item in page:
                try: results.append(json.loads(item.get("value", "{}")))
                except (TypeError, json.JSONDecodeError): continue
            if cursor is None: break
        return results

    def expire(self, instance_id: str, artifact_id: str) -> dict:
        record = self.get(instance_id, artifact_id)
        record["status"] = "EXPIRED"
        self._write(f"{self.PREFIX}/{instance_id}/{artifact_id}", record)
        return record

    def purge(self, instance_id: str, artifact_id: str) -> None:
        record = self.get(instance_id, artifact_id)
        if record["status"] != "EXPIRED":
            raise ArtifactError("NOT_EXPIRED", "只能清理 EXPIRED 状态的产物")
        self.store.kv_delete(f"{self.PREFIX}/{instance_id}/{artifact_id}")

    def check_expired(self, instance_id: str) -> list[str]:
        now = _now_iso()
        expired = []
        for record in self.list_for_instance(instance_id):
            if record["status"] == "ACTIVE" and record["retention_until"] <= now:
                self._notify(instance_id, record, "EXPIRE")
                self.expire(instance_id, record["artifact_id"])
                expired.append(record["artifact_id"])
        return expired

    def _group(self, instance_id: str) -> str:
        instance = self._read(f"jobflows/instances/{instance_id}/meta", {})
        template_id = instance.get("template_id", "") if isinstance(instance, dict) else ""
        template = self._read(f"jobflows/templates/{template_id}/meta", {})
        group_id = template.get("group_id", "") if isinstance(template, dict) else ""
        return str(group_id or "unassigned")

    def _notify(self, instance_id: str, record: dict, action: str) -> None:
        self.notification_service.notify(
            group_id=self._group(instance_id),
            resource_type="ARTIFACT",
            action=action,
            req_id=instance_id,
            run_id=str(record.get("attempt_id", "")),
            details={"artifact_id": record.get("artifact_id", "")},
        )


class ArtifactCleanupWorker:
    def __init__(self, artifacts: ArtifactService, *, interval: float = 3600.0):
        self.artifacts = artifacts
        self.interval = max(1.0, float(interval))
        self._stop = threading.Event()
        self.last_run: str | None = None
        self.last_result: dict[str, Any] = {}

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                log.exception("artifact cleanup tick failed")
            self._stop.wait(self.interval)

    def run_once(self) -> dict[str, Any]:
        items: list[dict] = []
        cursor = None
        while True:
            page, cursor = self.artifacts.store.kv_list(
                self.artifacts.PREFIX, cursor=cursor, limit=1000
            )
            items.extend(page)
            if cursor is None:
                break
        now = _now_iso()
        expired: list[str] = []
        seen: set[tuple[str, str]] = set()
        for item in items:
            try:
                record = json.loads(item.get("value", "{}"))
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(record, dict) or record.get("status") != "ACTIVE":
                continue
            if record.get("retention_until", "") > now:
                continue
            key = (record.get("instance_id", ""), record.get("artifact_id", ""))
            if key in seen:
                continue
            seen.add(key)
            self.artifacts._notify(record["instance_id"], record, "EXPIRE")
            self.artifacts.expire(record["instance_id"], record["artifact_id"])
            expired.append(record["artifact_id"])
        self.last_run = now
        self.last_result = {"expired": expired}
        return self.last_result
