"""Execution Profile data model and CRUD operations."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .auth import AuthenticationContext
from .kv_store_protocol import KVStore


class ProfileError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(f"{code}: {message}")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _revision_key(profile_id: str) -> str:
    return f"execution-profiles/{profile_id}/revision"


@dataclass
class ExecutionProfile:
    profile_id: str
    group_id: str
    name: str
    version: str
    agent_runtime_id: str
    workspace_policy: dict = field(default_factory=dict)
    resource_budget: dict = field(default_factory=dict)
    network_policy: dict = field(default_factory=dict)
    secret_policy: dict = field(default_factory=dict)
    skill_bundle_ids: list[str] = field(default_factory=list)
    mcp_grant_ids: list[str] = field(default_factory=list)
    status: str = "ACTIVE"
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""


class ExecutionProfileService:
    PREFIX = "execution-profiles"

    def __init__(self, store: KVStore):
        self.store = store

    def _read(self, key: str, default: Any = None) -> Any:
        raw, _ = self.store.kv_get(key)
        if not raw:
            return default
        return json.loads(raw)

    def _write(self, key: str, value: Any) -> None:
        self.store.kv_put(key, json.dumps(value, ensure_ascii=False))

    def create(self, body: dict, actor: str) -> dict:
        profile_id = body.get("profile_id") or f"prof-{int(time.time())}"
        group_id = body.get("group_id", "").strip()
        if not group_id:
            raise ProfileError("GROUP_ID_REQUIRED", "group_id 不能为空")
        if not body.get("name", "").strip():
            raise ProfileError("NAME_REQUIRED", "name 不能为空")
        if not body.get("agent_runtime_id", "").strip():
            raise ProfileError("RUNTIME_REQUIRED", "agent_runtime_id 不能为空")
        now = _now_iso()
        record = {
            "profile_id": profile_id,
            "group_id": group_id,
            "name": body["name"].strip(),
            "version": body.get("version", "1.0.0"),
            "agent_runtime_id": body["agent_runtime_id"],
            "workspace_policy": body.get("workspace_policy", {}),
            "resource_budget": body.get("resource_budget", {}),
            "network_policy": body.get("network_policy", {}),
            "secret_policy": body.get("secret_policy", {}),
            "skill_bundle_ids": body.get("skill_bundle_ids", []),
            "mcp_grant_ids": body.get("mcp_grant_ids", []),
            "status": "ACTIVE",
            "created_by": actor,
            "created_at": now,
            "updated_at": now,
        }
        self._write(f"{self.PREFIX}/{profile_id}", record)
        self._write(_revision_key(profile_id), 1)
        return record

    def get(self, profile_id: str) -> dict:
        record = self._read(f"{self.PREFIX}/{profile_id}")
        if not record:
            raise ProfileError("PROFILE_NOT_FOUND", f"Profile {profile_id} 不存在", 404)
        return record

    def list_for_group(self, group_id: str) -> list[dict]:
        results = []
        cursor = None
        while True:
            page, cursor = self.store.kv_list(f"{self.PREFIX}/", cursor=cursor, limit=1000)
            for item in page:
                key = item.get("key", "")
                if "/revision" in key:
                    continue
                try:
                    record = json.loads(item.get("value", "{}"))
                except (TypeError, json.JSONDecodeError):
                    continue
                if record.get("group_id") == group_id:
                    results.append(record)
            if cursor is None:
                break
        return results

    def update(self, profile_id: str, body: dict, actor: str) -> dict:
        record = self.get(profile_id)
        expected = body.pop("expected_revision", None)
        if expected is None:
            raise ProfileError("EXPECTED_REVISION_REQUIRED", "expected_revision 不能为空")
        current_rev = self._read(_revision_key(profile_id), 0)
        if expected != current_rev:
            raise ProfileError("REVISION_CONFLICT", f"Profile 已被修改 (revision={current_rev})", 409)
        for field_name in ("name", "version", "agent_runtime_id",
                           "workspace_policy", "resource_budget", "network_policy",
                           "secret_policy", "skill_bundle_ids", "mcp_grant_ids"):
            if field_name in body:
                record[field_name] = body[field_name]
        record["updated_at"] = _now_iso()
        self._write(f"{self.PREFIX}/{profile_id}", record)
        self._write(_revision_key(profile_id), current_rev + 1)
        return record

    def disable(self, profile_id: str, actor: str) -> dict:
        record = self.get(profile_id)
        record["status"] = "DISABLED"
        record["updated_at"] = _now_iso()
        self._write(f"{self.PREFIX}/{profile_id}", record)
        return record
