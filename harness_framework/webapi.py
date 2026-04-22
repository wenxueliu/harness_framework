"""
WebAPI — 为业务看板提供 HTTP 接口

虽然看板可以直连 Consul，但本模块仍提供少量增值接口：
- /api/workflows                  ← 一次性返回所有需求的聚合视图（看板首屏）
- /api/workflow/<req_id>          ← 单个需求的完整状态
- /api/workflow/<req_id>/control  ← POST 写入 PAUSE / RESUME / ABORT / RETRY
- /api/agents                     ← 当前所有注册 Agent 列表

零外部依赖，使用标准库 http.server。
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .consul_client import ConsulClient
from .message_bus import MessageBus, MessageStatus

log = logging.getLogger("webapi")


class APIHandler(BaseHTTPRequestHandler):
    consul: ConsulClient = None  # 由 server 实例注入
    message_bus: MessageBus = None  # 由 server 实例注入

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
                    if len(parts) >= 6 and "messages" in parts:
                        msg_idx = parts.index("messages")
                        if len(parts) > msg_idx + 1:
                            req_id = parts[msg_idx - 1]
                            task_name = parts[msg_idx + 1]
                            return self._get_messages(req_id, task_name)
                req_id = parts[-1]
                return self._get_workflow(req_id)
            if path.startswith("/api/sessions/"):
                # /api/sessions/<req_id>/<task>
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
            self._send_json(404, {"error": "not found"})
        except Exception as e:
            log.exception("POST %s failed", self.path)
            self._send_json(500, {"error": str(e)})

    # ── 业务接口 ────────────────────────────────────────────────────────────
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

        # 计算 phase 与进度
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
        feedback: dict = {}
        context: dict = {}
        control = ""

        prefix = f"workflows/{req_id}/"
        for it in items:
            rel = it["Key"][len(prefix):] if it["Key"].startswith(prefix) else it["Key"]
            parts = rel.split("/")
            if len(parts) >= 3 and parts[0] == "tasks":
                tasks.setdefault(parts[1], {})[parts[2]] = it.get("_decoded", "")
            elif len(parts) >= 3 and parts[0] == "feedback":
                feedback.setdefault(parts[1], {})[parts[2]] = it.get("_decoded", "")
            elif len(parts) >= 2 and parts[0] == "context":
                context["/".join(parts[1:])] = it.get("_decoded", "")
            elif rel == "control":
                control = it.get("_decoded", "")

        self._send_json(200, {
            "req_id": req_id,
            "control": control,
            "dependencies": dependencies,
            "tasks": tasks,
            "feedback": feedback,
            "context": context,
        })

    def _get_session_events(self, req_id: str, task_name: str):
        """返回指定任务的 Session 事件流。"""
        items, _ = self.consul.kv_get(
            f"workflows/{req_id}/sessions/{task_name}/", recurse=True
        )
        events = []
        if items:
            prefix = f"workflows/{req_id}/sessions/{task_name}/"
            for it in items:
                rel = it["Key"][len(prefix):] if it["Key"].startswith(prefix) else it["Key"]
                parts = rel.split("/")
                # 格式：<session_id>/events/<seq>
                if len(parts) >= 3 and parts[1] == "events":
                    events.append({
                        "session_id": parts[0],
                        "seq": int(parts[2]) if parts[2].isdigit() else 0,
                        "key": "/".join(parts[3:]),
                        "value": it.get("_decoded", ""),
                    })
        events.sort(key=lambda x: (x["session_id"], x["seq"]))
        self._send_json(200, {"req_id": req_id, "task": task_name, "events": events})

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
            self.consul.kv_put(f"workflows/{req_id}/tasks/{task}/status", "PENDING")
            self.consul.kv_delete(f"workflows/{req_id}/tasks/{task}/error_message")
        else:
            self.consul.kv_put(f"workflows/{req_id}/control", action)

        self._send_json(200, {"ok": True, "action": action, "req_id": req_id})

    def _get_messages(self, req_id: str, task_name: str):
        """获取指定任务队列中的消息"""
        status_str = parse_qs(urlparse(self.path).query).get("status", [None])[0]
        status = MessageStatus(status_str) if status_str else None

        messages = self.message_bus.poll(req_id, task_name, status=status)
        self._send_json(200, {
            "req_id": req_id,
            "task": task_name,
            "messages": [m.to_dict() for m in messages],
        })

    def _send_message(self, req_id: str, body: dict):
        """发送消息到目标任务队列"""
        from_task = body.get("from")
        to_task = body.get("to")
        action = body.get("action")
        params = body.get("params", {})
        timeout = body.get("timeout", 300)

        if not all([from_task, to_task, action]):
            return self._send_json(400, {"error": "from, to, action are required"})

        msg = self.message_bus.send(req_id, from_task, to_task, action, params, timeout)
        self._send_json(200, {"ok": True, "msg_id": msg.msg_id, "message": msg.to_dict()})


def serve(consul: ConsulClient, host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    APIHandler.consul = consul
    APIHandler.message_bus = MessageBus(consul)
    server = ThreadingHTTPServer((host, port), APIHandler)
    log.info("WebAPI serving on http://%s:%d/", host, port)
    return server
