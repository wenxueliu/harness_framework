"""
Watchdog — 任务超时检测与僵尸任务回收

负责：
- 检测 IN_PROGRESS 任务对应的 Agent 是否仍存活（Consul Health）
- Agent 死亡时，将任务回滚为 PENDING（保留重试次数）
- 检测 IN_PROGRESS 任务超时（默认 1 小时）
"""
from __future__ import annotations

import datetime
import json
import logging
import time

from .consul_client import ConsulClient

log = logging.getLogger("watchdog")


class Watchdog:
    def __init__(self, consul: ConsulClient,
                 poll_interval: int = 30,
                 task_timeout_seconds: int = 120,
                 heartbeat_timeout: int = 120,
                 max_retry: int = 3):
        self.consul = consul
        self.poll_interval = poll_interval
        self.task_timeout = task_timeout_seconds
        self.heartbeat_timeout = heartbeat_timeout
        self.max_retry = max_retry
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        log.info("Watchdog started, poll=%ss timeout=%ss",
                 self.poll_interval, self.task_timeout)
        while not self._stop:
            try:
                self._tick()
            except Exception as e:
                log.exception("watchdog tick error: %s", e)
            time.sleep(self.poll_interval)

    def _tick(self) -> None:
        # 收集存活 Agent
        alive = self._alive_agents()
        log.debug("alive agents: %s", alive)

        # 扫描所有需求的 IN_PROGRESS 任务
        items, _ = self.consul.kv_get("workflows/", recurse=True)
        if not items:
            return

        # 按 (req_id, task_name) 聚合
        tasks: dict = {}
        for it in items:
            parts = it["Key"].split("/")
            if len(parts) < 5 or parts[2] != "tasks":
                continue
            key = (parts[1], parts[3])
            tasks.setdefault(key, {})[parts[4]] = it.get("_decoded", "")

        for (req_id, task_name), meta in tasks.items():
            if meta.get("status") != "IN_PROGRESS":
                continue

            agent_id = meta.get("assigned_agent", "")
            started_at = meta.get("started_at", "")

            # 1. Agent 存活检查
            if agent_id and agent_id not in alive:
                log.warning("zombie task %s/%s (agent %s dead), recovering",
                            req_id, task_name, agent_id)
                self._recover(req_id, task_name, meta)
                continue

            # 2. 超时检查
            if started_at and self._is_overtime(started_at):
                log.warning("task %s/%s timed out (>%ss), recovering",
                            req_id, task_name, self.task_timeout)
                self._recover(req_id, task_name, meta, reason="timeout")
                continue

    def _alive_agents(self) -> set:
        """从 Consul Health 拉取所有 passing 的 agent-worker 实例 ID。"""
        services = self.consul.list_services("agent-worker")
        out = set()
        for svc in services:
            checks = svc.get("Checks", [])
            if all(c.get("Status") == "passing" for c in checks):
                sid = svc.get("Service", {}).get("ID", "")
                if sid:
                    out.add(sid)
        return out

    def _is_overtime(self, ts_str: str) -> bool:
        try:
            t = datetime.datetime.fromisoformat(ts_str.rstrip("Z"))
            age = (datetime.datetime.utcnow() - t).total_seconds()
            return age > self.task_timeout
        except Exception:
            return False

    def _recover(self, req_id: str, task_name: str, meta: dict,
                 reason: str = "agent_dead") -> None:
        base = f"workflows/{req_id}/tasks/{task_name}"
        # 记录回收原因
        self.consul.kv_put(f"{base}/last_recovery_reason", reason)
        self.consul.kv_put(f"{base}/last_recovery_at", _now_iso())

        # 增加重试计数
        cur, _ = self.consul.kv_get(f"{base}/retry_count")
        retry_count = int(cur or "0") + 1
        self.consul.kv_put(f"{base}/retry_count", str(retry_count))

        if retry_count >= self.max_retry:
            self.consul.kv_put(f"{base}/status", "FAILED")
            self.consul.kv_put(f"{base}/error_message",
                               f"Recovered {retry_count} times, exceeded limit")
            # 写入告警到 alerts/ 路径
            alert_key = f"alerts/{req_id}/{task_name}"
            self.consul.kv_put(alert_key, json.dumps({
                "reason": reason,
                "retry_count": retry_count,
                "failed_at": _now_iso(),
                "agent_id": meta.get("assigned_agent", ""),
            }))
            log.error("task %s/%s permanently failed after %d recoveries",
                      req_id, task_name, retry_count)
        else:
            # 回滚为 PENDING 重新分配
            self.consul.kv_put(f"{base}/status", "PENDING")
            # 清除上一次的 assigned_agent
            self.consul.kv_delete(f"{base}/assigned_agent")
            self.consul.kv_delete(f"{base}/started_at")


def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"
