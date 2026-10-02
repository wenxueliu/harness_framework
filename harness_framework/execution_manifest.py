"""Execution Manifest: immutable Attempt snapshot of resolved capabilities."""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from .kv_store_protocol import KVStore

def _now_iso(): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

class ManifestError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code = code; self.message = message; self.status = status
        super().__init__(f"{code}: {message}")

class ExecutionManifestService:
    PREFIX = "attempts"

    def __init__(self, store: KVStore):
        self.store = store

    def _read(self, key: str, default=None):
        raw, _ = self.store.kv_get(key)
        return json.loads(raw) if raw else default

    def _write(self, key: str, value):
        self.store.kv_put(key, json.dumps(value, ensure_ascii=False))

    def generate(
        self,
        attempt_id: str,
        agent_runtime: dict,
        execution_profile: dict,
        skill_bundles: list[dict],
        mcp_grants: list[dict],
        workspace_binding: dict,
        secret_refs: list[str] | None = None,
        previous_manifest_id: str | None = None,
    ) -> dict:
        key = f"{self.PREFIX}/{attempt_id}/manifest"
        existing = self._read(key)
        if existing:
            raise ManifestError("MANIFEST_ALREADY_EXISTS", f"Attempt {attempt_id} 已有 Manifest", 409)
        manifest_id = f"mf-{int(time.time())}-{attempt_id[:8]}"
        record = {
            "manifest_id": manifest_id,
            "attempt_id": attempt_id,
            "agent_runtime": agent_runtime,
            "execution_profile": execution_profile,
            "skill_bundles": skill_bundles,
            "mcp_grants": mcp_grants,
            "workspace_binding": workspace_binding,
            "secret_refs": secret_refs or [],
            "policy_versions": {
                "runtime": agent_runtime.get("version", "0.0.0"),
                "profile": execution_profile.get("version", "1.0.0"),
            },
            "resolved_at": _now_iso(),
            "resolved_by": "system" if not previous_manifest_id else "manual_retry",
            "previous_manifest_id": previous_manifest_id,
        }
        self._write(key, record)
        return record

    def get(self, attempt_id: str) -> dict:
        record = self._read(f"{self.PREFIX}/{attempt_id}/manifest")
        if not record:
            raise ManifestError("MANIFEST_NOT_FOUND", f"Attempt {attempt_id} 无 Manifest", 404)
        return record

    def diff(self, attempt_id: str) -> dict:
        current = self.get(attempt_id)
        prev_id = current.get("previous_manifest_id")
        if not prev_id:
            return {"changes": [], "summary": "首次执行，无前次 Manifest"}
        prev = self.get(prev_id.replace("mf-", "").split("-")[0]) if False else self._read_by_manifest_id(prev_id)
        if not prev:
            return {"changes": [], "summary": "前次 Manifest 已不存在"}
        changes = []
        for field in ("agent_runtime", "execution_profile", "workspace_binding"):
            old = prev.get(field, {})
            new = current.get(field, {})
            if old != new:
                changes.append({"field": field, "old": old.get("version", ""), "new": new.get("version", "")})
        old_skills = {s.get("bundle_id") for s in prev.get("skill_bundles", [])}
        new_skills = {s.get("bundle_id") for s in current.get("skill_bundles", [])}
        if old_skills != new_skills:
            changes.append({"field": "skill_bundles", "old": sorted(old_skills), "new": sorted(new_skills)})
        old_grants = {g.get("grant_id") for g in prev.get("mcp_grants", [])}
        new_grants = {g.get("grant_id") for g in current.get("mcp_grants", [])}
        if old_grants != new_grants:
            changes.append({"field": "mcp_grants", "old": sorted(old_grants), "new": sorted(new_grants)})
        return {"changes": changes, "summary": f"{len(changes)} 项差异"}

    def _read_by_manifest_id(self, manifest_id: str) -> Optional[dict]:
        cursor = None
        while True:
            page, cursor = self.store.kv_list(f"{self.PREFIX}/", cursor=cursor, limit=1000)
            for item in page:
                key = item.get("key", "")
                if "/manifest" not in key:
                    continue
                try:
                    record = json.loads(item.get("value", "{}"))
                except (TypeError, json.JSONDecodeError):
                    continue
                if record.get("manifest_id") == manifest_id:
                    return record
            if cursor is None:
                break
        return None
