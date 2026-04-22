"""
公共测试 fixtures — 提供 mock ConsulClient 与 KV 数据存储
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, Mock
from typing import Any

import pytest

from harness_framework.consul_client import ConsulClient


class MockConsulStore:
    """内存中的 Consul KV 模拟。"""

    def __init__(self, initial: dict[str, str] | None = None):
        self._store: dict[str, str] = dict(initial) if initial else {}
        self._index = 100

    def kv_get(self, key: str, recurse: bool = False) -> tuple[list[dict] | None, int]:
        self._index += 1
        if recurse:
            matches = []
            for k, v in self._store.items():
                if k.startswith(key):
                    matches.append({
                        "Key": k,
                        "Value": _encode(v),
                        "ModifyIndex": self._index,
                        "_decoded": v,
                    })
            if not matches:
                return None, self._index
            return matches, self._index
        else:
            v = self._store.get(key)
            if v is None:
                return None, self._index
            return {
                "Key": key,
                "Value": _encode(v),
                "ModifyIndex": self._index,
                "_decoded": v,
            }, self._index

    def kv_put(self, key: str, value: str, cas: int | None = None) -> bool:
        self._index += 1
        self._store[key] = value
        return True

    def kv_delete(self, key: str, recurse: bool = False) -> None:
        if recurse:
            self._store = {k: v for k, v in self._store.items() if not k.startswith(key)}
        else:
            self._store.pop(key, None)

    def list_services(self, service_name: str = "agent-worker") -> list[dict]:
        return []


def _encode(v: str) -> str:
    import base64
    return base64.b64encode(v.encode("utf-8")).decode("ascii")


@pytest.fixture
def mock_store():
    """空的数据存储。"""
    return MockConsulStore()


@pytest.fixture
def mock_consul(mock_store: MockConsulStore):
    """mock ConsulClient，方法指向 store。"""
    consul = MagicMock(spec=ConsulClient)
    consul.kv_get = Mock(side_effect=mock_store.kv_get)
    consul.kv_put = Mock(side_effect=mock_store.kv_put)
    consul.kv_delete = Mock(side_effect=mock_store.kv_delete)
    consul.list_services = Mock(side_effect=mock_store.list_services)
    consul.kv_blocking_get = Mock(return_value=(None, 1))
    return consul


@pytest.fixture
def sample_workflow():
    """标准测试用 workflow 数据。"""
    return {
        "workflows/req-001/dependencies": json.dumps({
            "design": {"type": "design", "depends_on": []},
            "backend": {"type": "backend", "depends_on": ["design"]},
            "test": {"type": "test", "depends_on": ["backend"]},
        }),
        "workflows/req-001/title": "用户登录功能",
        "workflows/req-001/tasks/design/status": "DONE",
        "workflows/req-001/tasks/design/type": "design",
        "workflows/req-001/tasks/backend/status": "BLOCKED",
        "workflows/req-001/tasks/backend/type": "backend",
        "workflows/req-001/tasks/test/status": "BLOCKED",
        "workflows/req-001/tasks/test/type": "test",
    }


@pytest.fixture
def mock_consul_with_workflow(mock_store: MockConsulStore, sample_workflow: dict):
    """预填充了 sample_workflow 的 mock Consul。"""
    for k, v in sample_workflow.items():
        mock_store.kv_put(k, v)
    consul = MagicMock(spec=ConsulClient)
    consul.kv_get = Mock(side_effect=mock_store.kv_get)
    consul.kv_put = Mock(side_effect=mock_store.kv_put)
    consul.kv_delete = Mock(side_effect=mock_store.kv_delete)
    consul.list_services = Mock(return_value=[])
    consul.kv_blocking_get = Mock(return_value=(None, 1))
    return consul
