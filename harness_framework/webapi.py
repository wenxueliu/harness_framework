"""
WebAPI — 为业务看板提供 HTTP 接口

虽然看板可以直连 Consul，但本模块仍提供少量增值接口：
- /api/workflows                  ← 一次性返回所有需求的聚合视图（看板首屏）
- /api/workflow/<req_id>          ← 单个需求的完整状态
- /api/workflow/<req_id>/control  ← POST 写入 PAUSE / RESUME / ABORT / RETRY
- /api/workflow/<req_id>/proposals ← GET 查看提案 / POST 确认或拒绝
- /api/agents                     ← 当前所有注册 Agent 列表

零外部依赖，使用标准库 http.server。
"""
from __future__ import annotations

import json
import logging
import threading
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .kv_store_protocol import KVStore
from .message_bus import MessageBus, MessageStatus
from .workflow_skills import WorkflowSkills
from .run_manager import RunManager

log = logging.getLogger("webapi")


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


class APIHandler(BaseHTTPRequestHandler):
    consul: KVStore = None
    message_bus: MessageBus = None
    run_manager: RunManager = None

    def log_message(self, format, *args):
        log.info("%s - %s", self.address_string(), format % args)

    def _send_json(self, code: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send_json(200, {"ok": True})

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/")
        try:
            if path == "/api/workflows":
                return self._list_workflows()
            if path.startswith("/api/workflow/"):
                parts = path.split("/")
                if "/messages/" in path:
                    msg_idx = parts.index("messages")
                    if len(parts) > msg_idx + 1:
                        req_id = parts[msg_idx - 1]
                        task_name = parts[msg_idx + 1]
                        return self._get_messages(req_id, task_name)
                if "/proposals" in path:
                    req_id = parts[-2]
                    return self._get_proposals(req_id)
                # /api/workflow/<req_id>/runs/<run_id>/sessions/export
                if len(parts) >= 7 and parts[3] == "runs" and parts[5] == "sessions" and parts[-1] == "export":
                    return self._export_run_sessions(parts[2], parts[4])
                # /api/workflow/<req_id>/runs/<run_id>/sessions
                if len(parts) >= 6 and parts[3] == "runs" and parts[5] == "sessions":
                    return self._get_run_sessions(parts[2], parts[4])
                # /api/workflow/<req_id>/runs/<run_id>/transitions
                if len(parts) >= 6 and parts[3] == "runs" and parts[-1] == "transitions":
                    return self._get_run_transitions(parts[2], parts[4])
                # /api/workflow/<req_id>/runs/<run_id>
                if len(parts) == 5 and parts[3] == "runs":
                    return self._get_run(parts[2], parts[4])
                # /api/workflow/<req_id>/runs
                if len(parts) == 4 and parts[3] == "runs":
                    return self._list_runs(parts[2])
                req_id = parts[-1]
                return self._get_workflow(req_id)
            if path.startswith("/api/sessions/"):
                parts = path.split("/")
                if len(parts) >= 4:
                    req_id, task_name = parts[2], parts[3]
                    return self._get_session_events(req_id, task_name)
                return self._send_json(400, {"error": "invalid sessions path"})
            if path == "/api/agents":
                return self._list_agents()
            if path == "/api/health":
                return self._send_json(200, {"ok": True, "service": "harness-framework"})
            self._send_json(404, {"error": "not found"})
        except Exception as e:
            log.exception("GET %s failed", self.path)
            self._send_json(500, {"error": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else {}

            if path.startswith("/api/workflow/") and path.endswith("/control"):
                req_id = path.split("/")[-2]
                return self._control(req_id, body)
            if path.startswith("/api/workflow/") and path.endswith("/messages"):
                req_id = path.split("/")[-2]
                return self._send_message(req_id, body)
            if path.startswith("/api/workflow/") and path.endswith("/proposals"):
                req_id = path.split("/")[-2]
                return self._confirm_proposal(req_id, body)
            parts = path.split("/")
            if (len(parts) == 7 and parts[1:3] == ["api", "workflow"]
                    and parts[4] == "task"
                    and parts[6] in {"approve", "reject"}):
                return self._review_decision(
                    parts[3], parts[5], parts[6], body
                )
            self._send_json(404, {"error": "not found"})
        except Exception as e:
            log.exception("POST %s failed", self.path)
            self._send_json(500, {"error": str(e)})

    def _list_workflows(self):
        items, _ = self.consul.kv_get("workflows/", recurse=True)
        if not items:
            return self._send_json(200, {"workflows": []})

        wfs: dict = {}
        for it in items:
            parts = it["Key"].split("/")
            if len(parts) < 2:
                continue
            req_id = parts[1]
            w = wfs.setdefault(req_id, {"req_id": req_id, "tasks": {}, "control": ""})
            if len(parts) >= 5 and parts[2] == "tasks":
                t = w["tasks"].setdefault(parts[3], {})
                t[parts[4]] = it.get("_decoded", "")
            elif len(parts) == 3 and parts[2] == "control":
                w["control"] = it.get("_decoded", "")
            elif len(parts) == 3 and parts[2] == "title":
                w["title"] = it.get("_decoded", "")

        result = []
        for req_id, w in wfs.items():
            tasks = w["tasks"]
            total = len(tasks)
            done = sum(1 for t in tasks.values() if t.get("status") == "DONE")
            failed = any(t.get("status") == "FAILED" for t in tasks.values())
            in_progress = any(t.get("status") == "IN_PROGRESS" for t in tasks.values())
            if total == 0:
                phase = "EMPTY"
            elif done == total:
                phase = "DONE"
            elif failed:
                phase = "FAILED"
            elif in_progress:
                phase = "RUNNING"
            else:
                phase = "PENDING"
            result.append({
                "req_id": req_id,
                "title": w.get("title", req_id),
                "control": w.get("control", ""),
                "total_tasks": total,
                "done_tasks": done,
                "phase": phase,
                "progress": round(done / total * 100, 1) if total else 0,
            })
        result.sort(key=lambda x: x["req_id"], reverse=True)
        self._send_json(200, {"workflows": result})

    def _get_workflow(self, req_id: str):
        items, _ = self.consul.kv_get(f"workflows/{req_id}/", recurse=True)
        if not items:
            return self._send_json(404, {"error": f"workflow {req_id} not found"})

        deps_str, _ = self.consul.kv_get(f"workflows/{req_id}/dependencies")
        dependencies = json.loads(deps_str) if deps_str else {}

        tasks: dict = {}
        context: dict = {}
        control = ""
        status = ""

        prefix = f"workflows/{req_id}/"
        for it in items:
            rel = it["Key"][len(prefix):] if it["Key"].startswith(prefix) else it["Key"]
            parts = rel.split("/")
            if len(parts) >= 3 and parts[0] == "tasks":
                tasks.setdefault(parts[1], {})[parts[2]] = it.get("_decoded", "")
            elif len(parts) >= 2 and parts[0] == "context":
                context["/".join(parts[1:])] = it.get("_decoded", "")
            elif rel == "control":
                control = it.get("_decoded", "")
            elif rel == "status":
                status = it.get("_decoded", "")

        self._send_json(200, {
            "req_id": req_id,
            "status": status,
            "control": control,
            "dependencies": dependencies,
            "tasks": tasks,
            "context": context,
        })

    def _list_runs(self, req_id: str):
        """GET /api/workflow/<req_id>/runs — 列出所有历史运行。"""
        runs = self.run_manager.list_runs(req_id)
        self._send_json(200, {"req_id": req_id, "runs": runs})

    def _get_run(self, req_id: str, run_id: str):
        """GET /api/workflow/<req_id>/runs/<run_id> — 获取运行详情。"""
        run = self.run_manager.get_run(req_id, run_id)
        if run is None:
            return self._send_json(404, {"error": f"run {run_id} not found"})
        transitions = self.run_manager.get_transitions(req_id, run_id)
        run["transition_count"] = len(transitions)
        self._send_json(200, {"req_id": req_id, "run": run})

    def _get_run_transitions(self, req_id: str, run_id: str):
        """GET /api/workflow/<req_id>/runs/<run_id>/transitions — 获取转换日志。"""
        run = self.run_manager.get_run(req_id, run_id)
        if run is None:
            return self._send_json(404, {"error": f"run {run_id} not found"})
        transitions = self.run_manager.get_transitions(req_id, run_id)
        self._send_json(200, {
            "req_id": req_id,
            "run_id": run_id,
            "transitions": transitions,
        })

    def _get_run_sessions(self, req_id: str, run_id: str):
        """GET .../runs/<run_id>/sessions — 列出 run 下所有 session 元数据。"""
        run = self.run_manager.get_run(req_id, run_id)
        if run is None:
            return self._send_json(404, {"error": f"run {run_id} not found"})
        sessions = self.run_manager.get_run_sessions(req_id, run_id)
        self._send_json(200, {
            "req_id": req_id,
            "run_id": run_id,
            "run_status": run.get("status", ""),
            "sessions": sessions,
        })

    def _export_run_sessions(self, req_id: str, run_id: str):
        """GET .../runs/<run_id>/sessions/export — 导出完整 session 数据。"""
        run = self.run_manager.get_run(req_id, run_id)
        if run is None:
            return self._send_json(404, {"error": f"run {run_id} not found"})
        data = self.run_manager.export_run_sessions(req_id, run_id)
        self._send_json(200, data)

    def _get_session_events(self, req_id: str, task_name: str):
        items, _ = self.consul.kv_get(
            f"workflows/{req_id}/sessions/{task_name}/", recurse=True
        )
        if not items:
            return self._send_json(200, {"req_id": req_id, "task": task_name, "events": []})

        prefix = f"workflows/{req_id}/sessions/{task_name}/"
        # 按 session_id 分组
        sessions: dict[str, dict] = {}
        for it in items:
            rel = it["Key"][len(prefix):] if it["Key"].startswith(prefix) else it["Key"]
            parts = rel.split("/")
            if len(parts) < 3 or parts[1] != "events":
                continue
            sid = parts[0]
            sessions.setdefault(sid, {"session_id": sid, "events": {}})

            if len(parts) == 3:
                # 新格式: events/<seq> → JSON blob
                try:
                    data = json.loads(it.get("_decoded", "{}"))
                    if isinstance(data, dict):
                        data["seq"] = parts[2]
                        sessions[sid]["events"][parts[2]] = data
                except json.JSONDecodeError:
                    pass
            elif len(parts) > 3:
                # 旧格式: events/<seq>/<field> → value
                seq = parts[2]
                field = "/".join(parts[3:])
                entry = sessions[sid]["events"].setdefault(seq, {"seq": seq})
                entry[field] = it.get("_decoded", "")

        # 扁平化为有序列表
        result: list[dict] = []
        for sid_data in sessions.values():
            evts = list(sid_data["events"].values())
            evts.sort(key=lambda e: str(e.get("seq", "")))
            result.extend(evts)

        self._send_json(200, {
            "req_id": req_id,
            "task": task_name,
            "events": result,
            "sessions": [{"session_id": s["session_id"], "event_count": len(s["events"])}
                         for s in sessions.values()],
        })

    def _list_agents(self):
        services = self.consul.list_services("agent-worker")
        agents = []
        for svc in services:
            s = svc.get("Service", {})
            checks = svc.get("Checks", [])
            healthy = all(c.get("Status") == "passing" for c in checks)
            agents.append({
                "agent_id": s.get("ID"),
                "tags": s.get("Tags", []),
                "meta": s.get("Meta", {}),
                "healthy": healthy,
            })
        self._send_json(200, {"agents": agents})

    def _control(self, req_id: str, body: dict):
        action = body.get("action", "").upper()
        if action not in ("PAUSE", "RESUME", "ABORT", "RETRY"):
            return self._send_json(400, {"error": "invalid action"})

        if action == "RESUME":
            self.consul.kv_delete(f"workflows/{req_id}/control")
        elif action == "RETRY":
            task = body.get("task_name", "")
            if not task:
                return self._send_json(400, {"error": "task_name required for RETRY"})
            prev_status, _ = self.consul.kv_get(
                f"workflows/{req_id}/tasks/{task}/status")
            self.consul.kv_put(f"workflows/{req_id}/tasks/{task}/status", "PENDING")
            self.consul.kv_delete(f"workflows/{req_id}/tasks/{task}/error_message")
            # 记录手动重试转换
            run_id = self.run_manager.get_or_create_run(req_id, "webapi")
            self.run_manager.record_transition(
                req_id, run_id, task,
                previous_state=prev_status or "",
                new_state="PENDING",
                actor="webapi",
                reason="manual retry",
            )
        elif action == "ABORT":
            self.consul.kv_put(f"workflows/{req_id}/control", action)
            # 记录被 abort 的任务转换
            run_id = self.run_manager.get_or_create_run(req_id, "webapi")
            tasks_meta = self._load_tasks_for_abort(req_id)
            for name, meta in tasks_meta.items():
                if meta.get("status") in ("", "PENDING", "IN_PROGRESS", "BLOCKED",
                                           "AWAITING_REVIEW"):
                    prev = meta.get("status", "")
                    self.run_manager.record_transition(
                        req_id, run_id, name,
                        previous_state=prev,
                        new_state="ABORTED",
                        actor="webapi",
                        reason="ABORT control signal via API",
                    )
                    self.consul.kv_put(
                        f"workflows/{req_id}/tasks/{name}/status", "ABORTED")
            self.run_manager.check_run_completion(req_id, run_id)
        else:
            self.consul.kv_put(f"workflows/{req_id}/control", action)

        self._send_json(200, {"ok": True, "action": action, "req_id": req_id})

    def _review_decision(self, req_id: str, task_name: str,
                         action: str, body: dict):
        """Approve or reject a task waiting at the human review gate."""
        actor = body.get("actor", "")
        comment = body.get("comment", "")
        if not isinstance(actor, str) or not actor.strip():
            return self._send_json(400, {"error": "actor is required"})
        if not isinstance(comment, str):
            return self._send_json(400, {"error": "comment must be a string"})
        if action == "reject" and not comment.strip():
            return self._send_json(
                400, {"error": "comment is required when rejecting"}
            )

        base = f"workflows/{req_id}/tasks/{task_name}"
        status, status_index = self.consul.kv_get(f"{base}/status")
        if status != "AWAITING_REVIEW":
            return self._send_json(
                409, {"error": f"task status is {status}, expected AWAITING_REVIEW"}
            )

        decision = {
            "decision": action.upper(),
            "actor": actor,
            "comment": comment,
            "decided_at": _now_iso(),
        }
        history_key = decision["decided_at"].replace(":", "-")
        self.consul.kv_put(
            f"{base}/review/human_decisions/{history_key}",
            json.dumps(decision, ensure_ascii=False),
        )
        run_id = self.run_manager.get_or_create_run(req_id, actor)

        if action == "approve":
            if not self.consul.kv_put(f"{base}/status", "DONE", cas=status_index):
                return self._send_json(409, {"error": "task changed concurrently"})
            self.consul.kv_put(f"{base}/review/human_decision", "APPROVE")
            self.consul.kv_put(f"{base}/approved_by", actor)
            self.run_manager.record_transition(
                req_id, run_id, task_name, "AWAITING_REVIEW", "DONE",
                actor, "human approval",
            )
            self.run_manager.check_run_completion(req_id, run_id)
            return self._send_json(200, {
                "ok": True, "status": "DONE", "decision": decision,
            })

        feedback = {
            "source": "human",
            "actor": actor,
            "verdict": "CHANGES_REQUIRED",
            "comment": comment,
            "observed_at": decision["decided_at"],
        }
        if not self.consul.kv_put(f"{base}/status", "PENDING", cas=status_index):
            return self._send_json(409, {"error": "task changed concurrently"})
        self.consul.kv_put(
            f"{base}/review/human_feedback",
            json.dumps(feedback, ensure_ascii=False),
        )
        # Fence the completed attempt before another worker claims the task.
        self.consul.kv_put(f"{base}/attempt_id", "")
        self.consul.kv_delete(f"{base}/assigned_agent")
        self.consul.kv_delete(f"{base}/evidence/review", recurse=True)
        self.run_manager.record_transition(
            req_id, run_id, task_name, "AWAITING_REVIEW", "PENDING",
            actor, "human requested changes", metadata={"comment": comment},
        )
        return self._send_json(200, {
            "ok": True, "status": "PENDING", "decision": decision,
        })

    def _load_tasks_for_abort(self, req_id: str) -> dict:
        """加载任务状态（用于 abort 时判断哪些任务需要标记为 ABORTED）。"""
        items, _ = self.consul.kv_get(
            f"workflows/{req_id}/tasks/", recurse=True
        )
        out: dict = {}
        if not items:
            return out
        for it in items:
            parts = it["Key"].split("/")
            if len(parts) < 5:
                continue
            name = parts[3]
            field = parts[4]
            out.setdefault(name, {})[field] = it.get("_decoded", "")
        return out

    def _get_messages(self, req_id: str, task_name: str):
        status_str = parse_qs(urlparse(self.path).query).get("status", [None])[0]
        status = MessageStatus(status_str) if status_str else None

        messages = self.message_bus.poll(req_id, task_name, status=status)
        self._send_json(200, {
            "req_id": req_id,
            "task": task_name,
            "messages": [m.to_dict() for m in messages],
        })

    def _send_message(self, req_id: str, body: dict):
        from_task = body.get("from")
        to_task = body.get("to")
        action = body.get("action")
        params = body.get("params", {})
        timeout = body.get("timeout", 300)

        if not all([from_task, to_task, action]):
            return self._send_json(400, {"error": "from, to, action are required"})

        msg = self.message_bus.send(req_id, from_task, to_task, action, params, timeout)
        self._send_json(200, {"ok": True, "msg_id": msg.msg_id, "message": msg.to_dict()})

    def _get_proposals(self, req_id: str):
        """获取当前待确认的提案"""
        skills = WorkflowSkills(self.consul)
        proposals = skills.list_pending_proposals(req_id)
        status = skills.check_workflow_status(req_id)
        self._send_json(200, {
            "req_id": req_id,
            "status": status,
            "proposals": proposals,
        })

    def _confirm_proposal(self, req_id: str, body: dict):
        """确认或拒绝 Proposal"""
        skills = WorkflowSkills(self.consul)
        action = body.get("action", "").lower()

        if action == "reject":
            result = skills.reject_proposal(req_id)
        else:
            accepted = body.get("accepted_tasks")
            rejected = body.get("rejected_tasks")
            result = skills.confirm_proposal(req_id, accepted, rejected)

        if result["success"]:
            return self._send_json(200, result)
        return self._send_json(400, result)


def serve(consul: KVStore, host: str = "0.0.0.0", port: int = 8080,
          run_manager: RunManager = None) -> ThreadingHTTPServer:
    APIHandler.consul = consul
    APIHandler.message_bus = MessageBus(consul)
    APIHandler.run_manager = run_manager or RunManager(consul)
    server = ThreadingHTTPServer((host, port), APIHandler)
    log.info("WebAPI serving on http://%s:%d/", host, port)
    return server
