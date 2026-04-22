"""
Message Bus — 任务间消息通信机制

基于 Consul KV 实现异步消息队列，支持：
- 消息发送：任务可以向其他任务发送请求
- 消息轮询：任务轮询自己的队列获取待处理消息
- 消息状态更新：处理完成后更新消息状态

队列结构：workflows/<req_id>/requests/task-<task_name>/<msg_id>
"""
from __future__ import annotations

import datetime
import json
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional

from .consul_client import ConsulClient


class MessageStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


@dataclass
class Message:
    msg_id: str
    req_id: str
    from_task: str
    to_task: str
    action: str
    params: dict = field(default_factory=dict)
    status: MessageStatus = MessageStatus.PENDING
    result: Optional[dict] = None
    created_at: str = ""
    timeout: int = 300

    def __post_init__(self):
        if not self.created_at:
            self.created_at = _now_iso()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value if isinstance(self.status, MessageStatus) else self.status
        d["from"] = d.pop("from_task")
        d["to"] = d.pop("to_task")
        return d

    @classmethod
    def from_dict(cls, data: dict) -> Message:
        status = data.get("status", "PENDING")
        if isinstance(status, str):
            status = MessageStatus(status)
        return cls(
            msg_id=data["msg_id"],
            req_id=data["req_id"],
            from_task=data["from"],
            to_task=data["to"],
            action=data["action"],
            params=data.get("params", {}),
            status=status,
            result=data.get("result"),
            created_at=data.get("created_at", ""),
            timeout=data.get("timeout", 300),
        )


class MessageBus:
    def __init__(self, consul: ConsulClient):
        self.consul = consul

    def send(self, req_id: str, from_task: str, to_task: str,
             action: str, params: Optional[dict] = None,
             timeout: int = 300) -> Message:
        """发送消息到目标任务的队列"""
        msg_id = f"msg-{uuid.uuid4().hex[:12]}"
        msg = Message(
            msg_id=msg_id,
            req_id=req_id,
            from_task=from_task,
            to_task=to_task,
            action=action,
            params=params or {},
            timeout=timeout,
        )

        key = f"workflows/{req_id}/requests/{to_task}/{msg_id}"
        self.consul.kv_put(key, json.dumps(msg.to_dict(), ensure_ascii=False))
        return msg

    def poll(self, req_id: str, task_name: str,
             status: Optional[MessageStatus] = None,
             limit: int = 10) -> list[Message]:
        """轮询任务队列中的消息，按创建时间排序（FIFO）"""
        prefix = f"workflows/{req_id}/requests/{task_name}/"
        items, _ = self.consul.kv_get(prefix, recurse=True)

        if not items:
            return []

        messages = []
        for item in items:
            try:
                data = json.loads(item.get("_decoded", "{}"))
                msg = Message.from_dict(data)

                if status and msg.status != status:
                    continue

                messages.append(msg)
            except (json.JSONDecodeError, KeyError):
                continue

        messages.sort(key=lambda m: m.created_at)
        return messages[:limit]

    def get(self, req_id: str, task_name: str, msg_id: str) -> Optional[Message]:
        """获取指定消息"""
        key = f"workflows/{req_id}/requests/{task_name}/{msg_id}"
        data, _ = self.consul.kv_get(key)
        if not data:
            return None
        return Message.from_dict(json.loads(data))

    def claim(self, msg_id: str, req_id: str, task_name: str) -> bool:
        """认领消息，标记为 PROCESSING（使用 CAS 保证原子性）"""
        key = f"workflows/{req_id}/requests/{task_name}/{msg_id}"
        data, idx = self.consul.kv_get(key)
        if not data:
            return False

        msg = Message.from_dict(json.loads(data))
        if msg.status != MessageStatus.PENDING:
            return False

        msg.status = MessageStatus.PROCESSING
        return self.consul.kv_put(key, json.dumps(msg.to_dict(), ensure_ascii=False), cas=idx)

    def complete(self, msg_id: str, req_id: str, task_name: str,
                 result: Optional[dict] = None) -> bool:
        """标记消息为完成"""
        key = f"workflows/{req_id}/requests/{task_name}/{msg_id}"
        data, idx = self.consul.kv_get(key)
        if not data:
            return False

        msg = Message.from_dict(json.loads(data))
        msg.status = MessageStatus.DONE
        msg.result = result
        return self.consul.kv_put(key, json.dumps(msg.to_dict(), ensure_ascii=False), cas=idx)

    def fail(self, msg_id: str, req_id: str, task_name: str,
             error: str) -> bool:
        """标记消息为失败"""
        key = f"workflows/{req_id}/requests/{task_name}/{msg_id}"
        data, idx = self.consul.kv_get(key)
        if not data:
            return False

        msg = Message.from_dict(json.loads(data))
        msg.status = MessageStatus.FAILED
        msg.result = {"error": error}
        return self.consul.kv_put(key, json.dumps(msg.to_dict(), ensure_ascii=False), cas=idx)

    def check_timeout(self, req_id: str, task_name: str) -> list[Message]:
        """检查超时的消息，标记为 TIMEOUT"""
        prefix = f"workflows/{req_id}/requests/{task_name}/"
        items, _ = self.consul.kv_get(prefix, recurse=True)

        if not items:
            return []

        timed_out = []
        now = datetime.datetime.utcnow()

        for item in items:
            try:
                data = json.loads(item.get("_decoded", "{}"))
                msg = Message.from_dict(data)

                if msg.status not in (MessageStatus.PENDING, MessageStatus.PROCESSING):
                    continue

                created = datetime.datetime.fromisoformat(msg.created_at.rstrip("Z"))
                age = (now - created).total_seconds()

                if age > msg.timeout:
                    key = f"workflows/{req_id}/requests/{task_name}/{msg.msg_id}"
                    msg.status = MessageStatus.TIMEOUT
                    self.consul.kv_put(key, json.dumps(msg.to_dict(), ensure_ascii=False))
                    timed_out.append(msg)
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

        return timed_out


def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"
