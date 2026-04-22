"""
Aggregator — DAG 状态聚合与任务推进

负责：
- 监听 workflows/<req_id>/tasks/*/status 变更
- 当某任务进入 DONE 时，检查下游任务的依赖是否全部满足，满足则将其设为 PENDING
- 当 test 任务 FAILED 且所有 feedback FIXED 时，重置 test 任务为 PENDING
- 处理 control 信号：PAUSE / RESUME / ABORT
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

from .consul_client import ConsulClient

log = logging.getLogger("aggregator")


class Aggregator:
    def __init__(self, consul: ConsulClient, poll_interval: int = 5):
        self.consul = consul
        self.poll_interval = poll_interval
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        log.info("Aggregator started, poll interval=%ss", self.poll_interval)
        while not self._stop:
            try:
                self._tick()
            except Exception as e:
                log.exception("aggregator tick error: %s", e)
            time.sleep(self.poll_interval)

    # ── 主循环 ──────────────────────────────────────────────────────────────
    def _tick(self) -> None:
        # 列出所有需求
        items, _ = self.consul.kv_get("workflows/", recurse=True)
        if not items:
            return

        req_ids = set()
        for it in items:
            parts = it["Key"].split("/")
            if len(parts) >= 2 and parts[0] == "workflows":
                req_ids.add(parts[1])

        # 按 priority 降序排列需求，高优先级先处理
        def req_priority(req_id: str) -> int:
            val, _ = self.consul.kv_get(f"workflows/{req_id}/priority")
            return int(val) if val else 0

        sorted_reqs = sorted(req_ids, key=req_priority, reverse=True)

        for req_id in sorted_reqs:
            try:
                self._process_requirement(req_id)
            except Exception as e:
                log.exception("process %s failed: %s", req_id, e)

    def _process_requirement(self, req_id: str) -> None:
        # 检查是否已发布
        pub_val, _ = self.consul.kv_get(f"workflows/{req_id}/published")
        if pub_val != "true":
            return  # 草稿模式，跳过

        # 控制信号
        ctl, _ = self.consul.kv_get(f"workflows/{req_id}/control")
        if ctl == "PAUSE":
            return  # 暂停时不推进任务
        if ctl == "ABORT":
            self._abort(req_id)
            return

        deps_str, _ = self.consul.kv_get(f"workflows/{req_id}/dependencies")
        if not deps_str:
            return
        try:
            deps = json.loads(deps_str)
        except json.JSONDecodeError:
            log.error("dependencies for %s is invalid JSON", req_id)
            return

        tasks_meta = self._load_tasks(req_id)

        for task_name, info in deps.items():
            self._maybe_activate(req_id, task_name, info, tasks_meta, deps)
            self._maybe_retest(req_id, task_name, info, tasks_meta)

    def _load_tasks(self, req_id: str) -> dict:
        """读取 req_id 下所有 tasks/<name>/status 等元数据。"""
        items, _ = self.consul.kv_get(f"workflows/{req_id}/tasks/", recurse=True)
        out: dict = {}
        if not items:
            return out
        for it in items:
            parts = it["Key"].split("/")
            # workflows/<req>/tasks/<name>/<field>
            if len(parts) < 5:
                continue
            name = parts[3]
            field = parts[4]
            out.setdefault(name, {})[field] = it.get("_decoded", "")
        return out

    def _maybe_activate(self, req_id: str, task_name: str, info: dict,
                        tasks_meta: dict, deps: dict) -> None:
        meta = tasks_meta.get(task_name, {})
        cur_status = meta.get("status", "")
        node_type = info.get("type", "task")

        # Parallel / Aggregate 节点由独立逻辑处理
        if node_type in ("parallel", "aggregate"):
            self._maybe_activate_composite(req_id, task_name, info, tasks_meta, deps)
            return

        # 只有未初始化或 BLOCKED 的任务可以被激活
        if cur_status not in ("", "BLOCKED"):
            return

        upstream = info.get("depends_on", [])
        if not all(tasks_meta.get(u, {}).get("status") == "DONE" for u in upstream):
            # 依赖未全部完成，标记 BLOCKED
            if cur_status == "":
                self.consul.kv_put(f"workflows/{req_id}/tasks/{task_name}/status", "BLOCKED")
            return

        # 依赖满足，激活为 PENDING
        log.info("activating task %s/%s", req_id, task_name)
        self.consul.kv_put(f"workflows/{req_id}/tasks/{task_name}/status", "PENDING")
        self.consul.kv_put(f"workflows/{req_id}/tasks/{task_name}/activated_at",
                           _now_iso())

    def _maybe_activate_composite(self, req_id: str, task_name: str,
                                  info: dict, tasks_meta: dict, deps: dict) -> None:
        """处理 parallel / aggregate 复合节点。"""
        meta = tasks_meta.get(task_name, {})
        cur_status = meta.get("status", "")
        node_type = info.get("type", "task")

        upstream = info.get("depends_on", [])
        all_up_done = all(
            tasks_meta.get(u, {}).get("status") == "DONE"
            for u in upstream
        )

        if node_type == "parallel":
            # Parallel 节点：依赖全部 DONE 时，将 children 全部激活为 PENDING
            if all_up_done and cur_status != "DONE":
                children = info.get("children", [])
                for child in children:
                    child_meta = tasks_meta.get(child, {})
                    if child_meta.get("status") in ("", "BLOCKED"):
                        self.consul.kv_put(
                            f"workflows/{req_id}/tasks/{child}/status", "PENDING")
                        self.consul.kv_put(
                            f"workflows/{req_id}/tasks/{child}/activated_at",
                            _now_iso())
                        log.info("parallel激活 child %s/%s", req_id, child)
                # 标记 parallel 自身为 DONE（children 已全部激活）
                self.consul.kv_put(
                    f"workflows/{req_id}/tasks/{task_name}/status", "DONE")
                log.info("parallel节点 %s/%s 完成", req_id, task_name)

        elif node_type == "aggregate":
            # Aggregate 节点：上游 parallel 全部 DONE 时，自身 DONE 并激活下游
            if all_up_done and cur_status != "DONE":
                self.consul.kv_put(
                    f"workflows/{req_id}/tasks/{task_name}/status", "DONE")
                log.info("aggregate节点 %s/%s 完成，激活下游", req_id, task_name)
                # 激活下游任务（depends_on 指向此 aggregate 的任务）
                for downstream, dinfo in tasks_meta.items():
                    # 跳过自身
                    if downstream == task_name:
                        continue
                    down_info = deps.get(downstream, {})
                    if task_name in down_info.get("depends_on", []):
                        if dinfo.get("status") in ("", "BLOCKED"):
                            self.consul.kv_put(
                                f"workflows/{req_id}/tasks/{downstream}/status",
                                "PENDING")
                            self.consul.kv_put(
                                f"workflows/{req_id}/tasks/{downstream}/activated_at",
                                _now_iso())
                            log.info("aggregate激活下游 %s/%s", req_id, downstream)

    def _maybe_retest(self, req_id: str, task_name: str, info: dict,
                      tasks_meta: dict) -> None:
        """若 test 任务 FAILED 且所有 feedback FIXED，则重置为 PENDING。"""
        meta = tasks_meta.get(task_name, {})
        if meta.get("status") != "FAILED":
            return
        if info.get("type") != "test":
            return

        # 检查反馈状态
        items, _ = self.consul.kv_get(f"workflows/{req_id}/feedback/", recurse=True)
        if not items:
            return

        services = {}
        for it in items:
            parts = it["Key"].split("/")
            if len(parts) >= 5 and parts[4] == "status":
                services[parts[3]] = it.get("_decoded", "")

        if not services:
            return
        if not all(s == "FIXED" for s in services.values()):
            return

        # 检查重试次数
        retry_count = int(meta.get("retry_count", "0") or "0")
        if retry_count >= 3:
            log.warning("task %s/%s exceeded retry limit", req_id, task_name)
            return

        log.info("re-triggering test task %s/%s (retry=%d)", req_id, task_name,
                 retry_count + 1)
        # 清除反馈
        self.consul.kv_delete(f"workflows/{req_id}/feedback/", recurse=True)
        # 重置任务
        self.consul.kv_put(f"workflows/{req_id}/tasks/{task_name}/status", "PENDING")
        self.consul.kv_put(f"workflows/{req_id}/tasks/{task_name}/retry_count",
                           str(retry_count + 1))
        self.consul.kv_delete(f"workflows/{req_id}/tasks/{task_name}/error_message")

    def _abort(self, req_id: str) -> None:
        """ABORT 信号：将所有非终态任务设为 ABORTED。"""
        tasks_meta = self._load_tasks(req_id)
        for name, meta in tasks_meta.items():
            if meta.get("status") in ("", "PENDING", "IN_PROGRESS", "BLOCKED",
                                      "AWAITING_REVIEW"):
                self.consul.kv_put(f"workflows/{req_id}/tasks/{name}/status",
                                   "ABORTED")
                log.info("aborted task %s/%s", req_id, name)


def _now_iso() -> str:
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"
