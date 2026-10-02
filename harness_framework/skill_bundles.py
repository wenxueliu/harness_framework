"""Skill Bundle data model and management."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .kv_store_protocol import KVStore

def _now_iso(): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

class SkillBundleError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code = code; self.message = message; self.status = status
        super().__init__(f"{code}: {message}")

class SkillBundleService:
    PREFIX = "skill-bundles"

    def __init__(self, store: KVStore):
        self.store = store

    def _read(self, key: str, default=None):
        raw, _ = self.store.kv_get(key)
        return json.loads(raw) if raw else default

    def _write(self, key: str, value):
        self.store.kv_put(key, json.dumps(value, ensure_ascii=False))

    def register(self, body: dict, actor: str) -> dict:
        content = body.get("content", "")
        if not content:
            raise SkillBundleError("CONTENT_REQUIRED", "content 不能为空")
        if not body.get("name", "").strip():
            raise SkillBundleError("NAME_REQUIRED", "name 不能为空")
        bundle_id = f"sb-{hashlib.sha256(content.encode()).hexdigest()[:12]}"
        existing = self._read(f"{self.PREFIX}/{bundle_id}")
        if existing:
            raise SkillBundleError("BUNDLE_EXISTS", "相同内容的 Bundle 已存在", 409)
        record = {
            "bundle_id": bundle_id,
            "name": body["name"].strip(),
            "version": body.get("version", "1.0.0"),
            "source": body.get("source", "platform"),
            "source_ref": body.get("source_ref", ""),
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "injection_method": body.get("injection_method", "workspace_rules"),
            "content": content,
            "uploaded_by": actor,
            "uploaded_at": _now_iso(),
        }
        self._write(f"{self.PREFIX}/{bundle_id}", record)
        return {k: v for k, v in record.items() if k != "content"}

    def get(self, bundle_id: str) -> dict:
        record = self._read(f"{self.PREFIX}/{bundle_id}")
        if not record:
            raise SkillBundleError("BUNDLE_NOT_FOUND", f"Bundle {bundle_id} 不存在", 404)
        return record

    def get_content(self, bundle_id: str) -> str:
        return self.get(bundle_id).get("content", "")

    def list_all(self) -> list[dict]:
        results = []
        cursor = None
        while True:
            page, cursor = self.store.kv_list(f"{self.PREFIX}/", cursor=cursor, limit=1000)
            for item in page:
                try:
                    record = json.loads(item.get("value", "{}"))
                    results.append({k: v for k, v in record.items() if k != "content"})
                except (TypeError, json.JSONDecodeError):
                    continue
            if cursor is None:
                break
        return results
