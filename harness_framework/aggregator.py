"""
负责：
- 监听 workflows/<req_id>/tasks/*/status 变更
- 当某任务进入 DONE 时，检查下游任务的依赖是否全部满足，满足则将其设为 PENDING
- 处理 control 信号：PAUSE / RESUME / ABORT

重测逻辑由 Test Agent 通过 Message Bus 自行管理，不在此组件中处理。

"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

from .kv_store_protocol import KVStore
from .run_manager import RunManager

log = logging.getLogger("aggregator")

SUCCESS_STATES = frozenset({"DONE"})
FAILURE_STATES = frozenset({"FAILED", "ABORTED", "SKIPPED_UPSTREAM_FAILED"})


class Aggregator:
    def __init__(self, consul: KVStore, run_manager: RunManager,
                 poll_interval: int = 5):
        self.consul = consul
        self.run_manager = run_manager
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

        workflow_status, _ = self.consul.kv_get(
            f"workflows/{req_id}/status"
        )
        if workflow_status == "Proposal":
            return  # DAG 正在变更，冻结推进直到人工确认或拒绝

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

        current_run, _ = self.consul.kv_get(f"workflows/{req_id}/current_run")
        if current_run:
            self.run_manager.check_run_completion(req_id, current_run)

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
        # 区分 blocking 和 non-blocking 依赖
        # blocking 默认 true（向后兼容）。若为 false，所有依赖均不阻塞。
        # 也支持 per-dependency 格式: depends_on 数组元素为 {"task": "x", "blocking": false}
        blocking_deps = []
        non_blocking_deps = []
        all_non_blocking = not info.get("blocking", True)

        for u in upstream:
            if isinstance(u, dict):
                task_name_u = u.get("task", "")
                if u.get("blocking", True) and not all_non_blocking:
                    blocking_deps.append(task_name_u)
                else:
                    non_blocking_deps.append(task_name_u)
            else:
                if all_non_blocking:
                    non_blocking_deps.append(u)
                else:
                    blocking_deps.append(u)

        failed_deps = [
            u for u in blocking_deps
            if tasks_meta.get(u, {}).get("status") in FAILURE_STATES
        ]
        if failed_deps:
            self._skip_for_upstream_failure(
                req_id, task_name, cur_status, failed_deps
            )
            return

        if not all(tasks_meta.get(u, {}).get("status") == "DONE" for u in blocking_deps):
            # 有阻塞依赖未完成，标记 BLOCKED
            if cur_status == "":
                self.consul.kv_put(f"workflows/{req_id}/tasks/{task_name}/status", "BLOCKED")
                run_id = self.run_manager.get_or_create_run(req_id, "aggregator")
                self.run_manager.record_transition(
                    req_id, run_id, task_name,
                    previous_state="",
                    new_state="BLOCKED",
                    actor="aggregator",
                    reason="dependencies not satisfied",
                )
            return

        # 依赖满足，激活为 PENDING
        log.info("activating task %s/%s", req_id, task_name)
        prev_status = cur_status if cur_status else ""
        self.consul.kv_put(f"workflows/{req_id}/tasks/{task_name}/status", "PENDING")
        self.consul.kv_put(f"workflows/{req_id}/tasks/{task_name}/activated_at",
                           _now_iso())
        run_id = self.run_manager.get_or_create_run(req_id, "aggregator")
        self.run_manager.record_transition(
            req_id, run_id, task_name,
            previous_state=prev_status,
            new_state="PENDING",
            actor="aggregator",
            reason="dependencies satisfied",
        )

    def _maybe_activate_composite(self, req_id: str, task_name: str,
                                  info: dict, tasks_meta: dict, deps: dict) -> None:
        """处理 parallel / aggregate 复合节点。"""
        meta = tasks_meta.get(task_name, {})
        cur_status = meta.get("status", "")
        node_type = info.get("type", "task")

        upstream = [_dependency_name(u) for u in info.get("depends_on", [])]
        upstream_states = [tasks_meta.get(u, {}).get("status") for u in upstream]
        all_up_done = all(state in SUCCESS_STATES for state in upstream_states)

        failed_upstream = [
            name for name, state in zip(upstream, upstream_states)
            if state in FAILURE_STATES
        ]
        if failed_upstream and cur_status in ("", "BLOCKED", "IN_PROGRESS"):
            self._skip_for_upstream_failure(
                req_id, task_name, cur_status, failed_upstream
            )
            return

        if node_type == "parallel":
            children = info.get("children", [])
            child_states = [
                tasks_meta.get(child, {}).get("status", "") for child in children
            ]

            # Parallel 是持久化 fork/join 节点：先激活 children，自身保持
            # IN_PROGRESS；只有 join policy 满足后才进入 DONE。
            if all_up_done and cur_status in ("", "BLOCKED"):
                run_id = self.run_manager.get_or_create_run(req_id, "aggregator")
                for child in children:
                    child_meta = tasks_meta.get(child, {})
                    if child_meta.get("status") in ("", "BLOCKED"):
                        prev_child = child_meta.get("status", "")
                        self.consul.kv_put(
                            f"workflows/{req_id}/tasks/{child}/status", "PENDING")
                        self.consul.kv_put(
                            f"workflows/{req_id}/tasks/{child}/activated_at",
                            _now_iso())
                        log.info("parallel激活 child %s/%s", req_id, child)
                        self.run_manager.record_transition(
                            req_id, run_id, child,
                            previous_state=prev_child,
                            new_state="PENDING",
                            actor="aggregator",
                            reason="parallel child activated",
                        )
                # 激活不等于完成。aggregate 必须等待 children 真正结束。
                self.consul.kv_put(
                    f"workflows/{req_id}/tasks/{task_name}/status", "IN_PROGRESS")
                log.info("parallel节点 %s/%s 已激活", req_id, task_name)
                self.run_manager.record_transition(
                    req_id, run_id, task_name,
                    previous_state=cur_status,
                    new_state="IN_PROGRESS",
                    actor="aggregator",
                    reason="all children activated; waiting for join",
                )

            if cur_status == "IN_PROGRESS":
                self._evaluate_parallel_join(
                    req_id, task_name, info, children, child_states
                )

        elif node_type == "aggregate":
            # Aggregate 节点：上游 parallel 全部 DONE 时，自身 DONE 并激活下游
            if all_up_done and cur_status != "DONE":
                run_id = self.run_manager.get_or_create_run(req_id, "aggregator")
                self.consul.kv_put(
                    f"workflows/{req_id}/tasks/{task_name}/status", "DONE")
                log.info("aggregate节点 %s/%s 完成，激活下游", req_id, task_name)
                self.run_manager.record_transition(
                    req_id, run_id, task_name,
                    previous_state=cur_status,
                    new_state="DONE",
                    actor="aggregator",
                    reason="upstream parallel tasks done",
                )
                # 激活下游任务（depends_on 指向此 aggregate 的任务）
                for downstream, dinfo in tasks_meta.items():
                    # 跳过自身
                    if downstream == task_name:
                        continue
                    down_info = deps.get(downstream, {})
                    if task_name in down_info.get("depends_on", []):
                        if dinfo.get("status") in ("", "BLOCKED"):
                            prev_down = dinfo.get("status", "")
                            self.consul.kv_put(
                                f"workflows/{req_id}/tasks/{downstream}/status",
                                "PENDING")
                            self.consul.kv_put(
                                f"workflows/{req_id}/tasks/{downstream}/activated_at",
                                _now_iso())
                            log.info("aggregate激活下游 %s/%s", req_id, downstream)
                            self.run_manager.record_transition(
                                req_id, run_id, downstream,
                                previous_state=prev_down,
                                new_state="PENDING",
                                actor="aggregator",
                                reason="aggregate activated downstream",
                            )

    def _evaluate_parallel_join(self, req_id: str, task_name: str, info: dict,
                                children: list[str], states: list[str]) -> None:
        """根据 join policy 完成或终止 parallel 节点。"""
        policy = info.get("join", {})
        if isinstance(policy, str):
            policy = {"strategy": policy}
        strategy = policy.get("strategy", "all")
        success_count = sum(state in SUCCESS_STATES for state in states)
        failure_count = sum(state in FAILURE_STATES for state in states)
        terminal_count = success_count + failure_count
        total = len(children)

        if strategy == "any":
            satisfied = success_count >= 1
            impossible = terminal_count == total and not satisfied
        elif strategy == "quorum":
            required = int(policy.get("minimum_success", total))
            if required < 1 or required > total:
                log.error("invalid quorum for %s/%s: %s", req_id, task_name, required)
                return
            satisfied = success_count >= required
            impossible = success_count + (total - terminal_count) < required
        else:
            satisfied = total > 0 and success_count == total
            impossible = failure_count > 0

        run_id = self.run_manager.get_or_create_run(req_id, "aggregator")
        base = f"workflows/{req_id}/tasks/{task_name}"
        self.consul.kv_put(f"{base}/children_done_count", str(success_count))
        self.consul.kv_put(f"{base}/children_terminal_count", str(terminal_count))

        if satisfied:
            self.consul.kv_put(f"{base}/status", "DONE")
            self.run_manager.record_transition(
                req_id, run_id, task_name, "IN_PROGRESS", "DONE", "aggregator",
                f"join policy satisfied ({strategy}: {success_count}/{total})",
            )
        elif impossible:
            self.consul.kv_put(f"{base}/status", "FAILED")
            self.consul.kv_put(
                f"{base}/error_message",
                f"join policy impossible ({strategy}: {success_count}/{total}, "
                f"failures={failure_count})",
            )
            self.run_manager.record_transition(
                req_id, run_id, task_name, "IN_PROGRESS", "FAILED", "aggregator",
                "parallel child failure made join policy impossible",
            )

    def _skip_for_upstream_failure(self, req_id: str, task_name: str,
                                   previous_state: str,
                                   failed_dependencies: list[str]) -> None:
        run_id = self.run_manager.get_or_create_run(req_id, "aggregator")
        base = f"workflows/{req_id}/tasks/{task_name}"
        reason = "upstream terminal failure: " + ", ".join(failed_dependencies)
        self.consul.kv_put(f"{base}/status", "SKIPPED_UPSTREAM_FAILED")
        self.consul.kv_put(f"{base}/error_message", reason)
        self.run_manager.record_transition(
            req_id, run_id, task_name,
            previous_state=previous_state,
            new_state="SKIPPED_UPSTREAM_FAILED",
            actor="aggregator",
            reason=reason,
        )

    def _abort(self, req_id: str) -> None:
        """ABORT 信号：将所有非终态任务设为 ABORTED。"""
        tasks_meta = self._load_tasks(req_id)
        run_id = self.run_manager.get_or_create_run(req_id, "aggregator")
        for name, meta in tasks_meta.items():
            if meta.get("status") in ("", "PENDING", "IN_PROGRESS", "BLOCKED",
                                      "AWAITING_REVIEW"):
                prev = meta.get("status", "")
                self.consul.kv_put(f"workflows/{req_id}/tasks/{name}/status",
                                   "ABORTED")
                log.info("aborted task %s/%s", req_id, name)
                self.run_manager.record_transition(
                    req_id, run_id, name,
                    previous_state=prev,
                    new_state="ABORTED",
                    actor="aggregator",
                    reason="ABORT control signal",
                )
        self.run_manager.check_run_completion(req_id, run_id)


def _now_iso() -> str:
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"


def _dependency_name(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("task", ""))
    return str(value)
