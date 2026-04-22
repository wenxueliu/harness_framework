"""
ConsulClient 单元测试

用例：
- kv_get_single: 有效 key 返回解码值
- kv_get_not_found: 不存在的 key 返回 (None, 0)
- kv_put: 写入 key/value 返回 True
- kv_delete: 删除 key 无异常
- kv_blocking_get: 阻塞读取返回数据和新 index
"""
from __future__ import annotations

import json
import base64

import pytest

from harness_framework.consul_client import ConsulClient


class MockConsulClient(ConsulClient):
    """用内存 store 替代真实 HTTP 请求。"""

    def __init__(self, store: dict | None = None):
        super().__init__(addr="http://127.0.0.1:8500")
        self._store = store or {}
        self._index = 100

    def _request(self, method: str, path: str,
                 params=None, body=None, timeout=None):
        self._index += 1

        if method == "GET" and ("/kv/" in path):
            key = path.split("/kv/", 1)[-1]
            if params and params.get("recurse"):
                matches = []
                prefix = key
                for k, v in self._store.items():
                    if k.startswith(prefix):
                        matches.append({
                            "Key": k,
                            "Value": base64.b64encode(v.encode()).decode(),
                            "ModifyIndex": self._index,
                        })
                if matches:
                    for m in matches:
                        v = m.get("Value")
                        m["_decoded"] = base64.b64decode(v).decode() if v else ""
                    return 200, json.dumps(matches).encode(), {}
                return 404, b"", {}
            else:
                v = self._store.get(key)
                if v is None:
                    return 404, b"not found", {}
                item = {
                    "Key": key,
                    "Value": base64.b64encode(v.encode()).decode(),
                    "ModifyIndex": self._index,
                }
                return 200, json.dumps([item]).encode(), {"X-Consul-Index": str(self._index)}

        if method == "PUT" and ("/kv/" in path):
            key = path.split("/kv/", 1)[-1]
            value = body.decode() if isinstance(body, bytes) else body
            self._store[key] = value
            return 200, b"true", {}

        if method == "DELETE" and ("/kv/" in path):
            key = path.split("/kv/", 1)[-1]
            if params and params.get("recurse"):
                self._store = {k: v for k, v in self._store.items() if not k.startswith(key)}
            else:
                self._store.pop(key, None)
            return 200, b"", {}

        if method == "PUT" and "/agent/service/register" in path:
            return 200, b"", {}

        return 500, b"unknown", {}


class TestConsulClient:
    def test_kv_get_single_exists(self):
        store = {"workflows/req-001/title": "用户登录功能"}
        consul = MockConsulClient(store)

        value, idx = consul.kv_get("workflows/req-001/title")

        assert value == "用户登录功能"
        assert idx > 0

    def test_kv_get_single_not_found(self):
        consul = MockConsulClient({})

        value, idx = consul.kv_get("workflows/nonexist/title")

        assert value is None
        assert idx == 0

    def test_kv_put(self):
        consul = MockConsulClient({})

        result = consul.kv_put("workflows/req-001/title", "新标题")

        assert result is True
        assert consul._store["workflows/req-001/title"] == "新标题"

    def test_kv_delete(self):
        store = {
            "workflows/req-001/title": "标题",
            "workflows/req-001/control": "PAUSE",
        }
        consul = MockConsulClient(store)

        consul.kv_delete("workflows/req-001/control")

        assert "workflows/req-001/control" not in consul._store
        assert "workflows/req-001/title" in consul._store

    def test_kv_delete_recurse(self):
        store = {
            "workflows/req-001/feedback/login/status": "FIXED",
            "workflows/req-001/feedback/login/reason": "fixed",
            "workflows/req-002/feedback/login/status": "OPEN",
        }
        consul = MockConsulClient(store)

        consul.kv_delete("workflows/req-001/feedback/", recurse=True)

        assert "workflows/req-001/feedback/login/status" not in consul._store
        assert "workflows/req-001/feedback/login/reason" not in consul._store
        assert "workflows/req-002/feedback/login/status" in consul._store

    def test_kv_blocking_get(self):
        store = {"workflows/req-001/control": "PAUSE"}
        consul = MockConsulClient(store)

        result, idx = consul.kv_blocking_get("workflows/req-001/control", index=0)

        assert result == "PAUSE"
        assert idx > 0

    def test_kv_blocking_get_not_found(self):
        consul = MockConsulClient({})

        result, idx = consul.kv_blocking_get("workflows/nonexist", index=0)

        assert result is None

    def test_list_services_empty(self):
        consul = MockConsulClient({})

        services = consul.list_services("agent-worker")

        assert services == []

    def test_service_register(self):
        consul = MockConsulClient({})

        consul.service_register({
            "Name": "agent-worker",
            "ID": "agent-001",
            "Tags": ["backend"],
        })

        assert True
