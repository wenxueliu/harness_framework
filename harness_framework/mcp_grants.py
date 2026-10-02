"""MCP Server registration and Grant management."""
from __future__ import annotations

import json
import time
from typing import Any

from .kv_store_protocol import KVStore

def _now_iso(): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

class MCPError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code = code; self.message = message; self.status = status
        super().__init__(f"{code}: {message}")

class MCPService:
    SERVER_PREFIX = "mcp-servers"
    GRANT_PREFIX = "mcp-grants"

    def __init__(self, store: KVStore):
        self.store = store

    def _read(self, key: str, default=None):
        raw, _ = self.store.kv_get(key)
        return json.loads(raw) if raw else default

    def _write(self, key: str, value):
        self.store.kv_put(key, json.dumps(value, ensure_ascii=False))

    def register_server(self, body: dict, actor: str) -> dict:
        server_id = body.get("server_id") or f"mcp-{int(time.time())}"
        if not body.get("name", "").strip():
            raise MCPError("NAME_REQUIRED", "name 不能为空")
        record = {
            "server_id": server_id,
            "name": body["name"].strip(),
            "version": body.get("version", "0.0.0"),
            "transport": body.get("transport", "stdio"),
            "endpoint": body.get("endpoint"),
            "health_endpoint": body.get("health_endpoint"),
            "tool_names": body.get("tool_names", []),
            "registered_by": actor,
            "registered_at": _now_iso(),
        }
        self._write(f"{self.SERVER_PREFIX}/{server_id}", record)
        return record

    def get_server(self, server_id: str) -> dict:
        record = self._read(f"{self.SERVER_PREFIX}/{server_id}")
        if not record:
            raise MCPError("SERVER_NOT_FOUND", f"MCP Server {server_id} 不存在", 404)
        return record

    def list_servers(self) -> list[dict]:
        results = []
        cursor = None
        while True:
            page, cursor = self.store.kv_list(f"{self.SERVER_PREFIX}/", cursor=cursor, limit=1000)
            for item in page:
                try: results.append(json.loads(item.get("value", "{}")))
                except (TypeError, json.JSONDecodeError): continue
            if cursor is None: break
        return results

    def create_grant(self, body: dict, actor: str) -> dict:
        grant_id = body.get("grant_id") or f"grant-{int(time.time())}"
        server_id = body.get("server_id", "").strip()
        group_id = body.get("group_id", "").strip()
        if not server_id or not group_id:
            raise MCPError("SERVER_AND_GROUP_REQUIRED", "server_id 和 group_id 不能为空")
        self.get_server(server_id)
        record = {
            "grant_id": grant_id,
            "server_id": server_id,
            "group_id": group_id,
            "profile_id": body.get("profile_id", ""),
            "expose_tools": body.get("expose_tools", []),
            "invoke_tools": body.get("invoke_tools", []),
            "status": "ACTIVE",
            "granted_by": actor,
            "granted_at": _now_iso(),
        }
        self._write(f"{self.GRANT_PREFIX}/{group_id}/{grant_id}", record)
        return record

    def get_grant(self, group_id: str, grant_id: str) -> dict:
        record = self._read(f"{self.GRANT_PREFIX}/{group_id}/{grant_id}")
        if not record:
            raise MCPError("GRANT_NOT_FOUND", f"Grant {grant_id} 不存在", 404)
        return record

    def revoke_grant(self, group_id: str, grant_id: str, actor: str) -> dict:
        record = self.get_grant(group_id, grant_id)
        record["status"] = "REVOKED"
        record["revoked_by"] = actor
        record["revoked_at"] = _now_iso()
        self._write(f"{self.GRANT_PREFIX}/{group_id}/{grant_id}", record)
        return record
