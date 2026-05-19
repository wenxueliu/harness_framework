"""
RunManager — 运行生命周期管理 + 状态转换审计日志

负责:
- 管理 workflow 的 run 生命周期（创建、复用、终止）
- 以追加方式记录每次任务状态转换（不覆盖）
- 提供历史 run 和转换记录的查询接口

设计原则:
- 仅 Python 标准库，零外部依赖
- 所有状态存储在 Consul KV 中
- 不突破现有模块的职责边界
"""
from __future__ import annotations

import datetime
import json
import logging
import time
import uuid
from typing import Any, Optional

from .kv_store_protocol import KVStore

log = logging.getLogger("run_manager")

RUN_TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "ABORTED", "SUPERSEDED"})


class RunManager:
    def __init__(self, consul: KVStore):
        self.consul = consul

    # ── Run 生命周期 ────────────────────────────────────────────────────────

    def get_or_create_run(self, req_id: str, actor: str) -> str:
        """获取当前活跃 run，若无则创建新的。返回 run_id。"""
        current, _ = self.consul.kv_get(f"workflows/{req_id}/current_run")
        if current:
            status, _ = self.consul.kv_get(
                f"workflows/{req_id}/runs/{current}/status"
            )
            if status and status not in RUN_TERMINAL_STATES:
                return current

        run_id = _generate_run_id()
        now = _now_iso()
        base = f"workflows/{req_id}/runs/{run_id}"
        self.consul.kv_put(f"{base}/status", "RUNNING")
        self.consul.kv_put(f"{base}/started_at", now)
        self.consul.kv_put(f"{base}/started_by", actor)
        self.consul.kv_put(f"{base}/summary", json.dumps(
            {"total": 0, "done": 0, "failed": 0, "aborted": 0}
        ))
        self.consul.kv_put(f"workflows/{req_id}/current_run", run_id)
        log.info("created run %s for workflow %s (actor=%s)", run_id, req_id, actor)
        return run_id

    def end_run(self, req_id: str, run_id: str, status: str) -> None:
        """以给定状态终止 run，清除 current_run 指针。"""
        now = _now_iso()
        base = f"workflows/{req_id}/runs/{run_id}"
        self.consul.kv_put(f"{base}/status", status)
        self.consul.kv_put(f"{base}/finished_at", now)
        self.consul.kv_delete(f"workflows/{req_id}/current_run")
        log.info("ended run %s for workflow %s: %s", run_id, req_id, status)

    def check_run_completion(self, req_id: str, run_id: str) -> None:
        """检查所有任务是否均已终态，若是则终止 run。"""
        tasks = self._load_tasks(req_id)
        if not tasks:
            return

        total = len(tasks)
        done = sum(1 for t in tasks.values() if t.get("status") == "DONE")
        failed = sum(1 for t in tasks.values() if t.get("status") == "FAILED")
        aborted = sum(1 for t in tasks.values() if t.get("status") == "ABORTED")
        terminal = done + failed + aborted

        base = f"workflows/{req_id}/runs/{run_id}"
        self.consul.kv_put(f"{base}/summary", json.dumps(
            {"total": total, "done": done, "failed": failed, "aborted": aborted}
        ))

        if terminal < total:
            return

        if aborted == total:
            self.end_run(req_id, run_id, "ABORTED")
        elif failed > 0 and terminal == total:
            self.end_run(req_id, run_id, "FAILED")
        else:
            self.end_run(req_id, run_id, "COMPLETED")

    # ── 转换审计日志 ────────────────────────────────────────────────────────

    def record_transition(
        self,
        req_id: str,
        run_id: str,
        task_name: str,
        previous_state: str,
        new_state: str,
        actor: str,
        reason: str = "",
        metadata: Optional[dict] = None,
    ) -> None:
        """追加一条状态转换记录到 run 的审计日志中。"""
        seq = _transition_seq()
        record = {
            "timestamp": _now_iso(),
            "task_name": task_name,
            "previous_state": previous_state,
            "new_state": new_state,
            "actor": actor,
            "reason": reason,
            "metadata": metadata or {},
        }
        key = f"workflows/{req_id}/runs/{run_id}/transitions/{seq}"
        self.consul.kv_put(key, json.dumps(record, ensure_ascii=False))
        log.debug("transition: %s/%s %s -> %s (%s)",
                  req_id, task_name, previous_state, new_state, actor)

    # ── Session 管理 ────────────────────────────────────────────────────────

    def record_session_start(
        self, req_id: str, run_id: str, task_name: str,
        session_id: str, agent_id: str
    ) -> None:
        """Agent 开始新 session 时写入索引条目。"""
        now = _now_iso()
        base = f"workflows/{req_id}/runs/{run_id}/sessions/{task_name}"
        self.consul.kv_put(f"{base}/session_id", session_id)
        self.consul.kv_put(f"{base}/agent_id", agent_id)
        self.consul.kv_put(f"{base}/started_at", now)
        self.consul.kv_put(f"{base}/status", "running")
        log.debug("session start: run=%s task=%s session=%s", run_id, task_name, session_id)

    def record_session_end(
        self, req_id: str, run_id: str, task_name: str,
        event_count: int = 0, error_count: int = 0,
        status: str = "completed", summary: str = ""
    ) -> None:
        """Agent 关闭 session 时更新索引条目。"""
        now = _now_iso()
        base = f"workflows/{req_id}/runs/{run_id}/sessions/{task_name}"
        self.consul.kv_put(f"{base}/ended_at", now)
        self.consul.kv_put(f"{base}/event_count", str(event_count))
        self.consul.kv_put(f"{base}/error_count", str(error_count))
        self.consul.kv_put(f"{base}/status", status)
        if summary:
            self.consul.kv_put(f"{base}/summary", summary)
        log.debug("session end: run=%s task=%s status=%s events=%d",
                  run_id, task_name, status, event_count)

    def get_run_sessions(self, req_id: str, run_id: str) -> list[dict]:
        """列出 run 下所有 task 的 session 元数据（按任务名排序）。"""
        items, _ = self.consul.kv_get(
            f"workflows/{req_id}/runs/{run_id}/sessions/", recurse=True
        )
        if not items:
            return []

        sessions_map: dict[str, dict] = {}
        prefix = f"workflows/{req_id}/runs/{run_id}/sessions/"
        for it in items:
            rel = it["Key"][len(prefix):]
            task_name, _, field = rel.partition("/")
            if not task_name:
                continue
            sessions_map.setdefault(task_name, {"task_name": task_name})
            sessions_map[task_name][field] = it.get("_decoded", "")

        result = list(sessions_map.values())
        result.sort(key=lambda s: s.get("task_name", ""))
        return result

    def export_run_sessions(self, req_id: str, run_id: str) -> dict:
        """导出 run 的完整 session 数据（含所有事件），用于分析和自进化。"""
        # 收集 session 元数据
        sessions = self.get_run_sessions(req_id, run_id)

        # 加载 DAG 以获得依赖顺序
        deps_str, _ = self.consul.kv_get(f"workflows/{req_id}/dependencies")
        dependencies = {}
        try:
            dependencies = json.loads(deps_str) if deps_str else {}
        except json.JSONDecodeError:
            pass

        # 加载任务状态
        tasks = self._load_tasks(req_id)

        # 收集每个 session 的完整事件
        session_events: dict[str, list] = {}
        for s in sessions:
            task_name = s.get("task_name", "")
            sid = s.get("session_id", "")
            if not task_name or not sid:
                continue
            events = self._load_session_events(req_id, task_name, sid)
            if events:
                session_events[task_name] = events

        # 按 DAG 顺序排列任务
        task_order = _topological_order(dependencies)

        run_info = self.get_run(req_id, run_id) or {}
        transitions = self.get_transitions(req_id, run_id)

        return {
            "req_id": req_id,
            "run_id": run_id,
            "run_status": run_info.get("status", ""),
            "started_at": run_info.get("started_at", ""),
            "finished_at": run_info.get("finished_at", ""),
            "started_by": run_info.get("started_by", ""),
            "summary": run_info.get("summary", {}),
            "task_order": task_order,
            "dependencies": dependencies,
            "tasks": tasks,
            "transitions": transitions,
            "sessions": sessions,
            "session_events": session_events,
        }

    # ── 查询方法 ────────────────────────────────────────────────────────────

    def list_runs(self, req_id: str) -> list[dict]:
        """列出 workflow 的所有历史 run（按 started_at 降序）。"""
        items, _ = self.consul.kv_get(
            f"workflows/{req_id}/runs/", recurse=True
        )
        if not items:
            return []

        runs_map: dict[str, dict] = {}
        prefix = f"workflows/{req_id}/runs/"
        for it in items:
            rel = it["Key"][len(prefix):]
            run_id, _, field = rel.partition("/")
            if not run_id:
                continue
            if field and "/" not in field:
                runs_map.setdefault(run_id, {"run_id": run_id})
                runs_map[run_id][field] = it.get("_decoded", "")

        result = []
        for run_id, data in runs_map.items():
            if "status" not in data:
                continue
            summary = data.get("summary", "{}")
            if isinstance(summary, str):
                try:
                    data["summary"] = json.loads(summary)
                except json.JSONDecodeError:
                    data["summary"] = {}
            result.append(data)

        result.sort(key=lambda r: str(r.get("started_at", "")), reverse=True)
        return result

    def get_run(self, req_id: str, run_id: str) -> Optional[dict]:
        """获取单个 run 的详情。"""
        items, _ = self.consul.kv_get(
            f"workflows/{req_id}/runs/{run_id}/", recurse=True
        )
        if not items:
            return None

        result: dict[str, Any] = {"run_id": run_id}
        prefix = f"workflows/{req_id}/runs/{run_id}/"
        for it in items:
            rel = it["Key"][len(prefix):]
            if "/" in rel:
                continue
            if rel == "summary":
                try:
                    result["summary"] = json.loads(it.get("_decoded", "{}"))
                except json.JSONDecodeError:
                    result["summary"] = {}
            else:
                result[rel] = it.get("_decoded", "")

        return result

    def get_transitions(self, req_id: str, run_id: str) -> list[dict]:
        """获取某个 run 的全部转换记录（按时间升序）。"""
        items, _ = self.consul.kv_get(
            f"workflows/{req_id}/runs/{run_id}/transitions/", recurse=True
        )
        if not items:
            return []

        transitions: list[dict] = []
        prefix = f"workflows/{req_id}/runs/{run_id}/transitions/"
        for it in items:
            rel = it["Key"][len(prefix):]
            try:
                data = json.loads(it.get("_decoded", "{}"))
                data["seq"] = rel
                transitions.append(data)
            except json.JSONDecodeError:
                continue

        transitions.sort(key=lambda t: str(t.get("timestamp", "")))
        return transitions

    # ── 内部方法 ────────────────────────────────────────────────────────────

    def _load_tasks(self, req_id: str) -> dict:
        """加载 workflow 下所有任务的状态。"""
        items, _ = self.consul.kv_get(
            f"workflows/{req_id}/tasks/", recurse=True
        )
        out: dict = {}
        if not items:
            return out
        for it in items:
            parts = it["Key"].split("/")
            # workflows/<req_id>/tasks/<name>/<field>
            if len(parts) < 5:
                continue
            name = parts[3]
            field = parts[4]
            out.setdefault(name, {})[field] = it.get("_decoded", "")
        return out

    def _load_session_events(self, req_id: str, task_name: str,
                             session_id: str) -> list[dict]:
        """加载单个 session 的完整事件列表。"""
        items, _ = self.consul.kv_get(
            f"workflows/{req_id}/sessions/{task_name}/{session_id}/events/",
            recurse=True
        )
        if not items:
            return []

        events: list[dict] = []
        prefix = f"workflows/{req_id}/sessions/{task_name}/{session_id}/events/"
        for it in items:
            rel = it["Key"][len(prefix):]
            try:
                # 新格式: JSON blob (log_step.py)
                data = json.loads(it.get("_decoded", "{}"))
                if isinstance(data, dict):
                    data["seq"] = rel
                    events.append(data)
            except json.JSONDecodeError:
                # 旧格式: 拆分的 kv pairs (worker.py)
                # 从 key 解析: events/<seq>/field
                parts = rel.split("/")
                if len(parts) == 2:
                    seq, field = parts[0], parts[1]
                    # 找到对应 seq 的已有事件，或创建新的
                    existing = None
                    for e in events:
                        if e.get("seq") == seq:
                            existing = e
                            break
                    if existing is None:
                        existing = {"seq": seq}
                        events.append(existing)
                    existing[field] = it.get("_decoded", "")

        events.sort(key=lambda e: str(e.get("seq", "")))
        return events


def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def _transition_seq() -> str:
    """生成严格单调递增的序列号（微秒精度，避免快速连续写入碰撞）。"""
    return f"{int(time.time() * 1000000):021d}"


def _generate_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


def _topological_order(deps: dict) -> list[str]:
    """返回依赖图的拓扑排序（同一层内字典序）。用于 session 导出排序。"""
    in_degree: dict[str, int] = {}
    adj: dict[str, list[str]] = {}

    for task_name, info in deps.items():
        in_degree.setdefault(task_name, 0)
        adj.setdefault(task_name, [])
        upstream = info.get("depends_on", [])
        for u in upstream:
            if isinstance(u, dict):
                u = u.get("task", "")
            if u:
                in_degree[task_name] = in_degree.get(task_name, 0) + 1
                adj.setdefault(u, []).append(task_name)
                in_degree.setdefault(u, 0)

    # 从入度为0的节点开始 BFS
    queue = sorted(n for n, d in in_degree.items() if d == 0)
    result: list[str] = []
    while queue:
        node = queue.pop(0)
        result.append(node)
        for neighbor in sorted(adj.get(node, [])):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    return result
