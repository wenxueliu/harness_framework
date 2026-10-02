"""Agent Runtime registration and health tracking."""
from __future__ import annotations

import json
import time
from typing import Any

from .kv_store_protocol import KVStore

def _now_iso(): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

class RuntimeError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code = code; self.message = message; self.status = status
        super().__init__(f"{code}: {message}")

class AgentRuntimeService:
    PREFIX = "agent-runtimes"

    def __init__(self, store: KVStore):
        self.store = store

    def _read(self, key: str, default=None):
        raw, _ = self.store.kv_get(key)
        return json.loads(raw) if raw else default

    def _write(self, key: str, value):
        self.store.kv_put(key, json.dumps(value, ensure_ascii=False))

    def register(self, body: dict, actor: str) -> dict:
        runtime_id = body.get("runtime_id") or f"rt-{int(time.time())}"
        if not body.get("name", "").strip():
            raise RuntimeError("NAME_REQUIRED", "name 不能为空")
        record = {
            "runtime_id": runtime_id,
            "name": body["name"].strip(),
            "version": body.get("version", "0.0.0"),
            "adapter_id": body.get("adapter_id", ""),
            "acp_version": body.get("acp_version", "1.2"),
            "supported_capabilities": body.get("supported_capabilities", []),
            "health_endpoint": body.get("health_endpoint", ""),
            "status": "OFFLINE",
            "registered_by": actor,
            "registered_at": _now_iso(),
        }
        self._write(f"{self.PREFIX}/{runtime_id}", record)
        return record

    def get(self, runtime_id: str) -> dict:
        record = self._read(f"{self.PREFIX}/{runtime_id}")
        if not record:
            raise RuntimeError("RUNTIME_NOT_FOUND", f"Runtime {runtime_id} 不存在", 404)
        return record

    def list_all(self) -> list[dict]:
        results = []
        cursor = None
        while True:
            page, cursor = self.store.kv_list(f"{self.PREFIX}/", cursor=cursor, limit=1000)
            for item in page:
                try:
                    results.append(json.loads(item.get("value", "{}")))
                except (TypeError, json.JSONDecodeError):
                    continue
            if cursor is None:
                break
        return results

    def update_health(self, runtime_id: str, status: str) -> dict:
        record = self.get(runtime_id)
        record["status"] = status
        record["last_health_check"] = _now_iso()
        self._write(f"{self.PREFIX}/{runtime_id}", record)
        return record
