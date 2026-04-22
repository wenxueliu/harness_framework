"""
Message Bus 单元测试

用例：
- send_message: 发送消息到目标队列
- poll_messages: 轮询消息，按创建时间排序（FIFO）
- claim_message: 认领消息，状态 PENDING → PROCESSING
- complete_message: 完成消息，写入结果
- fail_message: 标记消息失败
- check_timeout: 超时消息被标记
- poll_by_status: 按状态过滤消息
"""
from __future__ import annotations

import base64
import json
import time
from unittest.mock import MagicMock, Mock

import pytest

from harness_framework.message_bus import MessageBus, Message, MessageStatus


def _make_store(initial: dict) -> MagicMock:
    """构建适配 MessageBus 逻辑的 mock ConsulClient。"""
    store = dict(initial)
    indices = {k: 1 for k in store}

    def kv_get(key: str, recurse: bool = False):
        if recurse:
            prefix = key.rstrip("/") + "/"
            matches = []
            for k, v in store.items():
                if k.startswith(prefix):
                    matches.append({
                        "Key": k,
                        "Value": base64.b64encode(v.encode()).decode() if v else "",
                        "ModifyIndex": indices.get(k, 1),
                        "_decoded": v,
                    })
            if matches:
                return matches, 1
            return None, 0
        v = store.get(key)
        if v is not None:
            return v, indices.get(key, 1)
        return None, 0

    def kv_put(key: str, value: str, cas: int = None) -> bool:
        if cas is not None:
            current_idx = indices.get(key, 0)
            if current_idx != cas:
                return False
        store[key] = value
        indices[key] = indices.get(key, 1) + 1
        return True

    def kv_delete(key: str, recurse: bool = False) -> None:
        if recurse:
            prefix = key.rstrip("/") + "/"
            to_del = [k for k in store if k.startswith(prefix)]
            for k in to_del:
                del store[k]
        elif key in store:
            del store[key]

    consul = MagicMock()
    consul.kv_get = Mock(side_effect=kv_get)
    consul.kv_put = Mock(side_effect=kv_put)
    consul.kv_delete = Mock(side_effect=kv_delete)
    consul._store = store
    consul._indices = indices
    return consul


class TestMessageBus:
    def test_send_message(self):
        """发送消息到目标队列"""
        consul = _make_store({})
        bus = MessageBus(consul)

        msg = bus.send(
            req_id="req-001",
            from_task="task-frontend",
            to_task="task-backend",
            action="provide_api",
            params={"endpoint": "/api/user"},
            timeout=600,
        )

        assert msg.from_task == "task-frontend"
        assert msg.to_task == "task-backend"
        assert msg.action == "provide_api"
        assert msg.status == MessageStatus.PENDING
        assert msg.timeout == 600
        assert msg.msg_id.startswith("msg-")

        key = f"workflows/req-001/requests/{msg.to_task}/{msg.msg_id}"
        assert key in consul._store

    def test_poll_messages_fifo(self):
        """轮询消息，按创建时间排序（FIFO）"""
        store = {
            "workflows/req-001/requests/task-backend/msg-002": json.dumps({
                "msg_id": "msg-002",
                "req_id": "req-001",
                "from": "task-review",
                "to": "task-backend",
                "action": "review",
                "params": {},
                "status": "PENDING",
                "result": None,
                "created_at": "2025-04-22T10:02:00Z",
                "timeout": 300,
            }),
            "workflows/req-001/requests/task-backend/msg-001": json.dumps({
                "msg_id": "msg-001",
                "req_id": "req-001",
                "from": "task-frontend",
                "to": "task-backend",
                "action": "provide_api",
                "params": {},
                "status": "PENDING",
                "result": None,
                "created_at": "2025-04-22T10:00:00Z",
                "timeout": 300,
            }),
        }
        consul = _make_store(store)
        bus = MessageBus(consul)

        messages = bus.poll("req-001", "task-backend")

        assert len(messages) == 2
        assert messages[0].msg_id == "msg-001"
        assert messages[1].msg_id == "msg-002"

    def test_claim_message(self):
        """认领消息，状态从 PENDING → PROCESSING"""
        store = {
            "workflows/req-001/requests/task-backend/msg-001": json.dumps({
                "msg_id": "msg-001",
                "req_id": "req-001",
                "from": "task-frontend",
                "to": "task-backend",
                "action": "provide_api",
                "params": {},
                "status": "PENDING",
                "result": None,
                "created_at": "2025-04-22T10:00:00Z",
                "timeout": 300,
            }),
        }
        consul = _make_store(store)
        bus = MessageBus(consul)

        success = bus.claim("msg-001", "req-001", "task-backend")
        assert success

        data = json.loads(consul._store["workflows/req-001/requests/task-backend/msg-001"])
        assert data["status"] == "PROCESSING"

    def test_claim_already_processing(self):
        """已 PROCESSING 的消息不能重复认领"""
        store = {
            "workflows/req-001/requests/task-backend/msg-001": json.dumps({
                "msg_id": "msg-001",
                "req_id": "req-001",
                "from": "task-frontend",
                "to": "task-backend",
                "action": "provide_api",
                "params": {},
                "status": "PROCESSING",
                "result": None,
                "created_at": "2025-04-22T10:00:00Z",
                "timeout": 300,
            }),
        }
        consul = _make_store(store)
        bus = MessageBus(consul)

        success = bus.claim("msg-001", "req-001", "task-backend")
        assert not success

    def test_complete_message(self):
        """完成消息，写入结果"""
        store = {
            "workflows/req-001/requests/task-backend/msg-001": json.dumps({
                "msg_id": "msg-001",
                "req_id": "req-001",
                "from": "task-frontend",
                "to": "task-backend",
                "action": "provide_api",
                "params": {},
                "status": "PROCESSING",
                "result": None,
                "created_at": "2025-04-22T10:00:00Z",
                "timeout": 300,
            }),
        }
        consul = _make_store(store)
        bus = MessageBus(consul)

        result = {"endpoint": "/api/v1/user", "method": "GET"}
        success = bus.complete("msg-001", "req-001", "task-backend", result=result)
        assert success

        data = json.loads(consul._store["workflows/req-001/requests/task-backend/msg-001"])
        assert data["status"] == "DONE"
        assert data["result"] == result

    def test_fail_message(self):
        """标记消息失败"""
        store = {
            "workflows/req-001/requests/task-backend/msg-001": json.dumps({
                "msg_id": "msg-001",
                "req_id": "req-001",
                "from": "task-frontend",
                "to": "task-backend",
                "action": "provide_api",
                "params": {},
                "status": "PROCESSING",
                "result": None,
                "created_at": "2025-04-22T10:00:00Z",
                "timeout": 300,
            }),
        }
        consul = _make_store(store)
        bus = MessageBus(consul)

        success = bus.fail("msg-001", "req-001", "task-backend", "接口不存在")
        assert success

        data = json.loads(consul._store["workflows/req-001/requests/task-backend/msg-001"])
        assert data["status"] == "FAILED"
        assert data["result"]["error"] == "接口不存在"

    def test_check_timeout(self):
        """超时的 PENDING 消息被标记为 TIMEOUT"""
        import datetime
        old_time = (datetime.datetime.utcnow() - datetime.timedelta(minutes=10)).isoformat() + "Z"
        recent_time = datetime.datetime.utcnow().isoformat() + "Z"

        store = {
            "workflows/req-001/requests/task-backend/msg-001": json.dumps({
                "msg_id": "msg-001",
                "req_id": "req-001",
                "from": "task-frontend",
                "to": "task-backend",
                "action": "provide_api",
                "params": {},
                "status": "PENDING",
                "result": None,
                "created_at": old_time,
                "timeout": 300,
            }),
            "workflows/req-001/requests/task-backend/msg-002": json.dumps({
                "msg_id": "msg-002",
                "req_id": "req-001",
                "from": "task-review",
                "to": "task-backend",
                "action": "review",
                "params": {},
                "status": "PENDING",
                "result": None,
                "created_at": recent_time,
                "timeout": 300,
            }),
        }
        consul = _make_store(store)
        bus = MessageBus(consul)

        timed_out = bus.check_timeout("req-001", "task-backend")

        assert len(timed_out) == 1
        assert timed_out[0].msg_id == "msg-001"

        data = json.loads(consul._store["workflows/req-001/requests/task-backend/msg-001"])
        assert data["status"] == "TIMEOUT"

    def test_poll_by_status(self):
        """按状态过滤消息"""
        store = {
            "workflows/req-001/requests/task-backend/msg-001": json.dumps({
                "msg_id": "msg-001",
                "req_id": "req-001",
                "from": "task-frontend",
                "to": "task-backend",
                "action": "provide_api",
                "params": {},
                "status": "PENDING",
                "result": None,
                "created_at": "2025-04-22T10:00:00Z",
                "timeout": 300,
            }),
            "workflows/req-001/requests/task-backend/msg-002": json.dumps({
                "msg_id": "msg-002",
                "req_id": "req-001",
                "from": "task-review",
                "to": "task-backend",
                "action": "review",
                "params": {},
                "status": "DONE",
                "result": {},
                "created_at": "2025-04-22T10:01:00Z",
                "timeout": 300,
            }),
        }
        consul = _make_store(store)
        bus = MessageBus(consul)

        pending = bus.poll("req-001", "task-backend", status=MessageStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].msg_id == "msg-001"

        done = bus.poll("req-001", "task-backend", status=MessageStatus.DONE)
        assert len(done) == 1
        assert done[0].msg_id == "msg-002"

    def test_get_message(self):
        """获取指定消息"""
        store = {
            "workflows/req-001/requests/task-backend/msg-001": json.dumps({
                "msg_id": "msg-001",
                "req_id": "req-001",
                "from": "task-frontend",
                "to": "task-backend",
                "action": "provide_api",
                "params": {},
                "status": "PENDING",
                "result": None,
                "created_at": "2025-04-22T10:00:00Z",
                "timeout": 300,
            }),
        }
        consul = _make_store(store)
        bus = MessageBus(consul)

        msg = bus.get("req-001", "task-backend", "msg-001")
        assert msg is not None
        assert msg.msg_id == "msg-001"
        assert msg.action == "provide_api"

    def test_get_nonexistent_message(self):
        """获取不存在的消息返回 None"""
        consul = _make_store({})
        bus = MessageBus(consul)

        msg = bus.get("req-001", "task-backend", "msg-001")
        assert msg is None

    def test_poll_empty_queue(self):
        """空队列返回空列表"""
        consul = _make_store({})
        bus = MessageBus(consul)

        messages = bus.poll("req-001", "task-backend")
        assert messages == []
