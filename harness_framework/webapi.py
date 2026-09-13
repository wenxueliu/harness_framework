"""
WebAPI — 为业务看板提供 HTTP 接口

虽然看板可以直连 Consul，但本模块仍提供少量增值接口：
- /api/workflows                  ← 一次性返回所有需求的聚合视图（看板首屏）
- /api/workflow/<req_id>          ← 单个需求的完整状态
- /api/workflow/<req_id>/control  ← POST 写入 PAUSE / RESUME / ABORT / RETRY
- /api/workflow/<req_id>/task/<task>/messages ← GET/POST 人工任务消息
- /api/sessions/<req_id>/<task>   ← 分页、标准化的 Agent 执行时间线
- /api/workflow/<req_id>/proposals ← GET 查看提案 / POST 确认或拒绝
- /api/agents                     ← 当前所有注册 Agent 列表

零外部依赖，使用标准库 http.server。
"""
from __future__ import annotations

import json
import logging
import threading
import datetime
import uuid
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse, parse_qs

from .kv_store_protocol import KVStore
from .message_bus import MessageBus, MessageStatus
from .human_interaction import (
    create_human_message,
    finish_human_message,
    list_human_messages,
)
from .recovery import rewind_to_task
from .workflow_skills import WorkflowSkills
from .run_manager import RunManager
from .contracts import ReviewPolicy
from .auth import (
    AuthConfig,
    AuthenticationError,
    AuthorizationService,
    authenticate_request,
)
from .capabilities import CapabilitiesService, FeatureConfig
from .api_errors import APIError
from .project_groups import ProjectGroupService, UNASSIGNED_GROUP_ID
from .workspace_manager import WorkspaceManager
from .workspace_security import WorkspaceSecurity
from .workspace_files import WorkspaceFileService
from .event_journal import EventJournal
from .workspace_merge import WorkspaceMergeService

log = logging.getLogger("webapi")


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


class APIHandler(BaseHTTPRequestHandler):
    consul: KVStore = None
    message_bus: MessageBus = None
    run_manager: RunManager = None
    auth_config: AuthConfig = AuthConfig()
    authorization: AuthorizationService = None
    capabilities: CapabilitiesService = None
    project_groups: ProjectGroupService = None
    workspace_manager: WorkspaceManager = None
    workspace_files: WorkspaceFileService = None
    event_journal: EventJournal = None
    workspace_merge: WorkspaceMergeService = None
    sse_connection_seconds: float = 300.0

    def log_message(self, format, *args):
        log.info("%s - %s", self.address_string(), format % args)

    def _send_json(self, code: int, payload) -> None:
        request_id = getattr(self, "request_id", None) or f"http_{uuid.uuid4().hex}"
        self.request_id = request_id
        if isinstance(payload, dict) and "request_id" not in payload:
            payload = {**payload, "request_id": request_id}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Idempotency-Key, Last-Event-ID, Authorization",
        )
        self.send_header("X-Request-ID", request_id)
        self.send_header("Access-Control-Expose-Headers", "X-Request-ID")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send_json(200, {"ok": True})

    def _authentication_context(self):
        peer = self.client_address[0] if self.client_address else ""
        return authenticate_request(self.auth_config, self.headers, peer)

    def _send_error(
        self, code: int, error_code: str, message: str, details: dict | None = None
    ) -> None:
        self._send_json(code, {
            "error": {
                "code": error_code,
                "message": message,
                "details": details or {},
            }
        })

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2 * 1024 * 1024:
            raise APIError("REQUEST_TOO_LARGE", "请求正文过大", 413)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            body = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise APIError("INVALID_JSON", "请求正文不是有效 JSON", 400) from exc
        if not isinstance(body, dict):
            raise APIError("INVALID_JSON_OBJECT", "请求正文必须是 JSON 对象", 400)
        return body

    def _handle_api_error(self, error: APIError) -> None:
        self._send_error(error.status, error.code, error.message, dict(error.details))

    def _require(
        self, context, capability: str, group_id: str | None = None,
        req_id: str | None = None,
    ) -> None:
        if req_id is None:
            path = urlparse(getattr(self, "path", "")).path.split("/")
            if len(path) > 3 and path[1] in {"workflow", "workflows"}:
                req_id = unquote(path[2])
        try:
            self.authorization.require(context, capability, group_id, req_id)
        except PermissionError as exc:
            raise APIError(
                "FORBIDDEN", "当前用户没有执行此操作的权限", 403,
                {"capability": capability},
            ) from exc

    def _append_event(self, *args, **kwargs):
        """Append through the configured journal, including direct-handler tests."""
        journal = self.event_journal or EventJournal(self.consul)
        return journal.append(*args, **kwargs)

    def _workspace_context(self, parsed_url, workspace_id: str, context,
                           *, write: bool = False) -> dict:
        query = parse_qs(parsed_url.query)
        values = {
            key: query.get(key, [None])[0]
            for key in ("req_id", "run_id", "task_id", "attempt_id")
        }
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise APIError(
                "WORKSPACE_CONTEXT_REQUIRED", "Workspace 请求缺少 Attempt 上下文", 422,
                {"fields": missing},
            )
        run_workspace = self.workspace_manager.get_run_workspace(
            values["req_id"], values["run_id"], include_root=True
        )
        project_id = run_workspace.get("project_workspace_id")
        group_id = None
        if project_id:
            group_id = self.workspace_manager.get(project_id)["group_id"]
        self._require(context, "file:write" if write else "file:read", group_id)
        return {"workspace_id": workspace_id, **values}

    def _send_event_stream(self, parsed_url) -> None:
        context = self._authentication_context()
        query = parse_qs(parsed_url.query)
        filters = {
            key: query.get(key, [None])[0]
            for key in ("group_id", "req_id", "run_id", "task_id")
        }
        # A filtered stream is an authorization boundary, not merely a client
        # convenience. Trusted-proxy users cannot subscribe globally and infer
        # subjects belonging to groups they cannot read.
        group_id = filters["group_id"]
        explicit_group_filter = bool(group_id)
        req_id = filters["req_id"]
        if not group_id and req_id:
            group_id, _ = self.consul.kv_get(f"workflows/{req_id}/project-group")
            group_id = group_id or UNASSIGNED_GROUP_ID
        if context.mode != "local" and not group_id:
            raise APIError(
                "EVENT_SCOPE_REQUIRED",
                "事件订阅必须指定有权访问的项目组或 Workflow",
                422,
            )
        if group_id:
            self._require(context, "group:read", group_id)
            if explicit_group_filter:
                filters["group_id"] = group_id
        last_event_id = self.headers.get("Last-Event-ID") or None
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Request-ID", getattr(self, "request_id", ""))
        self.end_headers()
        deadline = time.monotonic() + self.sse_connection_seconds
        try:
            while time.monotonic() < deadline:
                events, reset = self.event_journal.replay(
                    after_event_id=last_event_id, limit=500
                )
                if reset:
                    payload = json.dumps({"reason": "EVENT_CURSOR_EXPIRED"})
                    self.wfile.write(
                        f"event: STREAM_RESET_REQUIRED\ndata: {payload}\n\n".encode()
                    )
                    self.wfile.flush()
                    self.close_connection = True
                    return
                delivered = False
                for event in events:
                    last_event_id = event.event_id
                    if any(value and event.subject.get(key) != value
                           for key, value in filters.items()):
                        continue
                    payload = json.dumps(event.to_dict(), ensure_ascii=False)
                    self.wfile.write(
                        f"id: {event.event_id}\nevent: {event.type}\ndata: {payload}\n\n".encode()
                    )
                    delivered = True
                if delivered:
                    self.wfile.flush()
                    continue
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
                _, index = self.event_journal.head()
                remaining = max(0.01, min(15.0, deadline - time.monotonic()))
                self.consul.kv_blocking_get(
                    f"{self.event_journal.base}/head", index=index,
                    wait=f"{remaining}s",
                )
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
            return

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/")
        self.request_id = self.headers.get("X-Request-ID") or f"http_{uuid.uuid4().hex}"
        try:
            if path == "/api/events":
                return self._send_event_stream(u)
            if path == "/api/capabilities":
                context = self._authentication_context()
                query = parse_qs(u.query)
                group_id = query.get("group_id", [None])[0]
                return self._send_json(200, self.capabilities.snapshot(
                    context,
                    group_id=group_id,
                    req_id=query.get("req_id", [None])[0],
                    run_id=query.get("run_id", [None])[0],
                    attempt_id=query.get("attempt_id", [None])[0],
                ))
            if path == "/api/project-groups":
                context = self._authentication_context()
                query = parse_qs(u.query)
                try:
                    limit = int(query.get("limit", ["100"])[0])
                except ValueError as exc:
                    raise APIError("INVALID_LIMIT", "limit 必须是整数", 400) from exc
                result = self.project_groups.list(
                    status=query.get("status", [None])[0],
                    cursor=query.get("cursor", [None])[0], limit=limit,
                )
                if context.mode != "local" and context.subject not in self.auth_config.platform_owners:
                    result["project_groups"] = [
                        group for group in result["project_groups"]
                        if group["group_id"] == UNASSIGNED_GROUP_ID
                        or "group:read" in self.authorization.capabilities_for(
                            context, group["group_id"]
                        )
                    ]
                return self._send_json(200, result)
            if path.startswith("/api/project-groups/"):
                context = self._authentication_context()
                parts = path.split("/")
                group_id = unquote(parts[3]) if len(parts) > 3 else ""
                self._require(context, "group:read", group_id)
                if len(parts) == 4:
                    return self._send_json(200, {
                        "project_group": self.project_groups.get(group_id)
                    })
                if len(parts) == 5 and parts[4] == "members":
                    return self._send_json(200, {
                        "members": self.project_groups.list_members(group_id)
                    })
                if len(parts) == 5 and parts[4] == "workflows":
                    return self._send_json(200, {
                        "workflows": self.project_groups.list_workflows(group_id)
                    })
                if len(parts) == 5 and parts[4] == "workspaces":
                    return self._send_json(200, {
                        "workspaces": self.workspace_manager.list_for_group(group_id)
                    })
            if path.startswith("/api/workspaces/"):
                context = self._authentication_context()
                parts = path.split("/")
                if len(parts) == 4:
                    workspace = self.workspace_manager.get(unquote(parts[3]))
                    self._require(context, "group:read", workspace["group_id"])
                    return self._send_json(200, {"workspace": workspace})
                if len(parts) == 5 and parts[4] in {"tree", "file", "search", "changes", "diff", "actions"}:
                    workspace_id = unquote(parts[3])
                    call_context = self._workspace_context(u, workspace_id, context)
                    query = parse_qs(u.query)
                    if parts[4] == "tree":
                        result = self.workspace_files.tree(
                            **call_context, path=query.get("path", [""])[0],
                            cursor=query.get("cursor", [None])[0],
                            limit=int(query.get("limit", ["200"])[0]),
                        )
                        return self._send_json(200, result)
                    if parts[4] == "file":
                        result = self.workspace_files.read_file(
                            **call_context, path=query.get("path", [""])[0]
                        )
                        return self._send_json(200, result)
                    if parts[4] == "search":
                        result = self.workspace_files.search(
                            **call_context, query=query.get("q", [""])[0],
                            limit=int(query.get("limit", ["100"])[0]),
                        )
                        return self._send_json(200, {"results": result})
                    if parts[4] == "actions":
                        return self._send_json(200, {"actions":
                            self.workspace_files.list_actions(**call_context)
                        })
                    if parts[4] == "diff":
                        return self._send_json(200, self.workspace_files.diff(
                            **call_context, path=query.get("path", [""])[0]
                        ))
                    return self._send_json(200, {"changes":
                        self.workspace_files.changes(**call_context)
                    })
            if path.startswith("/api/workflows/"):
                context = self._authentication_context()
                parts = path.split("/")
                req_id = unquote(parts[3]) if len(parts) > 3 else ""
                group_id, _ = self.consul.kv_get(f"workflows/{req_id}/project-group")
                self._require(context, "group:read", group_id or None)
                if (len(parts) == 8 and parts[4] == "runs"
                        and parts[6] == "merge-tasks"):
                    self._require(context, "workspace:merge", group_id or None, req_id)
                    return self._send_json(200, {"merge_task": self.workspace_merge.preview(
                        req_id, unquote(parts[5]), unquote(parts[7])
                    )})
                if (len(parts) == 7 and parts[4] == "runs"
                        and parts[6] == "workspace"):
                    return self._send_json(200, {"workspace":
                        self.workspace_manager.get_run_workspace(req_id, unquote(parts[5]))
                    })
                if (len(parts) == 8 and parts[4] == "runs"
                        and parts[6] == "workspace" and parts[7] == "manifest"):
                    return self._send_json(200, {"manifest":
                        self.workspace_manager.get_run_manifest(req_id, unquote(parts[5]))
                    })
                if (len(parts) == 7 and parts[4] == "tasks"
                        and parts[6] == "attempts"):
                    run_id = parse_qs(u.query).get("run_id", [None])[0]
                    if not run_id:
                        raise APIError("RUN_ID_REQUIRED", "run_id 不能为空", 422)
                    return self._send_json(200, {"attempts":
                        self.workspace_manager.list_attempts(
                            req_id, run_id, unquote(parts[5])
                        )
                    })
                if (len(parts) == 9 and parts[4] == "tasks"
                        and parts[6] == "attempts"):
                    run_id = parse_qs(u.query).get("run_id", [None])[0]
                    if not run_id:
                        raise APIError("RUN_ID_REQUIRED", "run_id 不能为空", 422)
                    return self._send_json(200, {"workspace_binding":
                        self.workspace_manager.get_attempt_binding(
                            req_id, run_id, unquote(parts[5]), unquote(parts[7])
                        )
                    })
            if path == "/api/workflows":
                context = self._authentication_context()
                return self._list_workflows(context)
            if path.startswith("/api/workflow/"):
                parts = path.split("/")
                context = self._authentication_context()
                candidate_req_id = parts[3] if len(parts) > 3 else parts[-1]
                group_id, _ = self.consul.kv_get(
                    f"workflows/{candidate_req_id}/project-group"
                )
                self._require(context, "group:read", group_id or None)
                if (len(parts) == 7 and parts[4] == "task"
                        and parts[6] == "messages"):
                    return self._get_task_messages(parts[3], parts[5])
                if "/messages/" in path:
                    msg_idx = parts.index("messages")
                    if len(parts) > msg_idx + 1:
                        req_id = parts[msg_idx - 1]
                        task_name = parts[msg_idx + 1]
                        return self._get_messages(req_id, task_name)
                if "/proposals" in path:
                    req_id = parts[-2]
                    return self._get_proposals(req_id)
                if len(parts) == 8 and parts[4] == "task" and parts[6] == "adaptive":
                    return self._adaptive_get(parts[3], parts[5], parts[7], u)
                # /api/workflow/<req_id>/runs/<run_id>/sessions/export
                if len(parts) == 8 and parts[4] == "runs" and parts[6] == "sessions" and parts[7] == "export":
                    return self._export_run_sessions(parts[3], parts[5])
                # /api/workflow/<req_id>/runs/<run_id>/sessions
                if len(parts) == 7 and parts[4] == "runs" and parts[6] == "sessions":
                    return self._get_run_sessions(parts[3], parts[5])
                # /api/workflow/<req_id>/runs/<run_id>/transitions
                if len(parts) == 7 and parts[4] == "runs" and parts[6] == "transitions":
                    return self._get_run_transitions(parts[3], parts[5])
                # /api/workflow/<req_id>/runs/<run_id>
                if len(parts) == 6 and parts[4] == "runs":
                    return self._get_run(parts[3], parts[5])
                # /api/workflow/<req_id>/runs
                if len(parts) == 5 and parts[4] == "runs":
                    return self._list_runs(parts[3])
                req_id = parts[3]
                return self._get_workflow(req_id)
            if path.startswith("/api/sessions/"):
                parts = path.split("/")
                if len(parts) >= 5:
                    req_id, task_name = parts[3], parts[4]
                    context = self._authentication_context()
                    group_id, _ = self.consul.kv_get(
                        f"workflows/{req_id}/project-group"
                    )
                    self._require(context, "group:read", group_id or None)
                    return self._get_session_events(req_id, task_name, u)
                return self._send_json(400, {"error": "invalid sessions path"})
            if path == "/api/agents":
                self._authentication_context()
                return self._list_agents()
            if path == "/api/health":
                return self._send_json(200, {"ok": True, "service": "harness-framework"})
            self._send_json(404, {"error": "not found"})
        except APIError as e:
            self._handle_api_error(e)
        except AuthenticationError as e:
            self._send_error(401, "UNAUTHENTICATED", str(e))
        except Exception as e:
            log.exception("GET %s failed", self.path)
            self._send_error(500, "INTERNAL_ERROR", "服务器内部错误")

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/")
        self.request_id = self.headers.get("X-Request-ID") or f"http_{uuid.uuid4().hex}"
        try:
            body = self._read_json_body()

            parts = path.split("/")
            if (len(parts) == 8 and parts[1:3] == ["api", "workflows"]
                    and parts[4] == "runs" and parts[6] == "workspace"
                    and parts[7] in {"cleanup", "trash", "restore", "purge"}):
                context = self._authentication_context()
                req_id, run_id, action = unquote(parts[3]), unquote(parts[5]), parts[7]
                group_id, _ = self.consul.kv_get(f"workflows/{req_id}/project-group")
                self._require(context, "workspace:policy", group_id or None)
                operations = {
                    "cleanup": self.workspace_manager.request_cleanup,
                    "trash": self.workspace_manager.trash_run_workspace,
                    "restore": self.workspace_manager.restore_run_workspace,
                    "purge": self.workspace_manager.purge_run_workspace,
                }
                workspace = operations[action](req_id, run_id)
                event = self._append_event(
                    "WORKSPACE_STATUS_CHANGED",
                    subject={"group_id": group_id or UNASSIGNED_GROUP_ID,
                             "req_id": req_id, "run_id": run_id,
                             "workspace_id": workspace["run_workspace_id"]},
                    actor={"type": "human", "id": context.subject},
                    data={"status": workspace["status"], "action": action},
                )
                return self._send_json(200, {"workspace": workspace,
                                              "event_id": event.event_id})
            if (len(parts) == 5 and parts[1:3] == ["api", "workflows"]
                    and parts[4] == "runs"):
                context = self._authentication_context()
                req_id = unquote(parts[3])
                dependencies, _ = self.consul.kv_get(f"workflows/{req_id}/dependencies")
                if dependencies is None:
                    raise APIError("WORKFLOW_NOT_FOUND", "Workflow 不存在", 404)
                published, _ = self.consul.kv_get(f"workflows/{req_id}/published")
                if published != "true":
                    raise APIError("WORKFLOW_NOT_PUBLISHED", "Workflow 尚未发布", 422)
                group_id, _ = self.consul.kv_get(f"workflows/{req_id}/project-group")
                self._require(context, "run:create", group_id or None)
                if group_id:
                    self.project_groups._require_active(group_id)
                idempotency_key = self.headers.get("Idempotency-Key")
                if not idempotency_key:
                    raise APIError(
                        "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key 不能为空", 422
                    )
                workspace_request = body.get("workspace")
                if not isinstance(workspace_request, dict):
                    raise APIError(
                        "WORKSPACE_SELECTION_REQUIRED", "请选择本次运行使用的工作区", 422
                    )
                self.consul.kv_put(
                    f"workflows/{req_id}/execution_mode", "managed-workspace"
                )
                run_id = self.run_manager.create_provisioning_run(
                    req_id, context.subject, idempotency_key=idempotency_key
                )
                existing_run = self.run_manager.get_run(req_id, run_id) or {}
                if existing_run.get("status") == "RUNNING":
                    return self._send_json(200, {
                        "run": existing_run,
                        "workspace": self.workspace_manager.get_run_workspace(
                            req_id, run_id
                        ),
                    })
                if existing_run.get("status") == "FAILED":
                    raise APIError(
                        "RUN_PROVISIONING_FAILED",
                        existing_run.get("error_message", "Run Workspace 准备失败"),
                        409,
                    )
                try:
                    workspace = self.workspace_manager.provision_run_workspace(
                        req_id=req_id, run_id=run_id,
                        project_workspace_id=workspace_request.get("project_workspace_id"),
                        strategy=str(workspace_request.get("strategy", "")),
                        git_ref=workspace_request.get("git_ref"),
                        accept_dirty=bool(workspace_request.get("accept_dirty", False)),
                    )
                    self.run_manager.activate_provisioned_run(req_id, run_id)
                except Exception as exc:
                    status, _ = self.consul.kv_get(
                        f"workflows/{req_id}/runs/{run_id}/status"
                    )
                    if status == "PROVISIONING":
                        self.run_manager.fail_provisioning_run(req_id, run_id, str(exc))
                    raise
                self._append_event(
                    "RUN_CREATED",
                    subject={"group_id": group_id or UNASSIGNED_GROUP_ID,
                             "req_id": req_id, "run_id": run_id},
                    actor={"type": "human", "id": context.subject},
                    data={"status": "RUNNING",
                          "workspace_id": workspace["run_workspace_id"]},
                )
                self._append_event(
                    "WORKSPACE_STATUS_CHANGED",
                    subject={"group_id": group_id or UNASSIGNED_GROUP_ID,
                             "req_id": req_id, "run_id": run_id},
                    actor={"type": "system", "id": "workspace-manager"},
                    data={"workspace_id": workspace["run_workspace_id"],
                          "status": workspace["status"]},
                )
                return self._send_json(201, {
                    "run": self.run_manager.get_run(req_id, run_id),
                    "workspace": workspace,
                })
            if (len(parts) == 7 and parts[1:3] == ["api", "workflows"]
                    and parts[4] == "runs" and parts[6] == "merge-tasks"):
                context = self._authentication_context()
                req_id, run_id = unquote(parts[3]), unquote(parts[5])
                group_id, _ = self.consul.kv_get(f"workflows/{req_id}/project-group")
                self._require(context, "workspace:merge", group_id or None, req_id)
                merge = self.workspace_merge.create_task(
                    req_id=req_id, run_id=run_id,
                    source_task_id=str(body.get("source_task_id", "")),
                    source_attempt_id=str(body.get("source_attempt_id", "")),
                    target_task_id=str(body.get("target_task_id", "")),
                    target_attempt_id=str(body.get("target_attempt_id", "")),
                    actor=context.subject, message=str(body.get("message", "")),
                )
                return self._send_json(201, {"merge_task": merge})
            if (len(parts) == 9 and parts[1:3] == ["api", "workflows"]
                    and parts[4] == "runs" and parts[6] == "merge-tasks"
                    and parts[8] == "apply"):
                context = self._authentication_context()
                req_id, run_id = unquote(parts[3]), unquote(parts[5])
                group_id, _ = self.consul.kv_get(f"workflows/{req_id}/project-group")
                self._require(context, "workspace:merge", group_id or None, req_id)
                merge = self.workspace_merge.apply(
                    req_id, run_id, unquote(parts[7]), actor=context.subject
                )
                return self._send_json(200, {"merge_task": merge})
            if path == "/api/project-groups":
                context = self._authentication_context()
                self._require(context, "group:manage")
                group = self.project_groups.create(
                    name=body.get("name", ""),
                    description=body.get("description", ""),
                    actor=context.subject,
                    policy=body.get("policy", {}),
                )
                return self._send_json(201, {"project_group": group})
            if (len(parts) == 5 and parts[1:3] == ["api", "project-groups"]
                    and parts[4] == "workflow-references"):
                context = self._authentication_context()
                group_id = unquote(parts[3])
                self._require(context, "group:manage", group_id)
                reference = self.project_groups.add_reference(
                    group_id, str(body.get("req_id", "")),
                    list(body.get("capabilities", [])),
                )
                return self._send_json(201, {"reference": reference})
            if (len(parts) == 5 and parts[1:3] == ["api", "project-groups"]
                    and parts[4] == "workspaces"):
                context = self._authentication_context()
                group_id = unquote(parts[3])
                self._require(context, "workspace:register", group_id)
                if body.get("source_type", "LOCAL_PATH") == "GIT_CLONE":
                    workspace = self.workspace_manager.register_git(
                        group_id=group_id, name=str(body.get("name", "")),
                        git_url=str(body.get("git_url", "")),
                        default_ref=str(body.get("default_ref", "main")),
                        access=str(body.get("access", "READ_WRITE")),
                        policy=body.get("policy", {}),
                    )
                else:
                    workspace = self.workspace_manager.register_local(
                        group_id=group_id,
                        name=str(body.get("name", "")),
                        root_alias=str(body.get("root_alias", "")),
                        relative_path=str(body.get("relative_path", "")),
                        access=str(body.get("access", "READ_WRITE")),
                        policy=body.get("policy", {}),
                    )
                return self._send_json(201, {"workspace": workspace})
            if (len(parts) == 5 and parts[1:3] == ["api", "workspaces"]
                    and parts[4] == "preflight"):
                context = self._authentication_context()
                workspace = self.workspace_manager.get(unquote(parts[3]))
                self._require(context, "workspace:register", workspace["group_id"])
                result = self.workspace_manager.preflight(workspace["workspace_id"])
                return self._send_json(200, {"preflight": result})
            if (len(parts) in {5, 6} and parts[1:3] == ["api", "workspaces"]
                    and parts[4] in {"checkpoints", "actions"}):
                context = self._authentication_context()
                workspace_id = unquote(parts[3])
                values = {key: body.get(key) for key in (
                    "req_id", "run_id", "task_id", "attempt_id"
                )}
                missing = [key for key, value in values.items() if not value]
                if missing:
                    raise APIError(
                        "WORKSPACE_CONTEXT_REQUIRED", "Workspace 请求缺少 Attempt 上下文",
                        422, {"fields": missing},
                    )
                run_workspace = self.workspace_manager.get_run_workspace(
                    values["req_id"], values["run_id"], include_root=True
                )
                project_id = run_workspace.get("project_workspace_id")
                group_id = self.workspace_manager.get(project_id)["group_id"] if project_id else None
                if parts[4] == "checkpoints":
                    self._require(context, "checkpoint:create", group_id)
                    result = self.workspace_files.create_checkpoint(
                        workspace_id=workspace_id, **values, actor=context.subject,
                        message=body.get("message", ""),
                        idempotency_key=self.headers.get("Idempotency-Key") or "",
                    )
                    return self._send_json(201, result)
                if len(parts) != 6:
                    raise APIError("ACTION_ID_REQUIRED", "actionId 不能为空", 422)
                self._require(context, "file:read", group_id)
                result = self.workspace_files.run_action(
                    workspace_id=workspace_id, **values,
                    action_id=unquote(parts[5]), actor=context.subject,
                )
                return self._send_json(200, result)
            if (len(parts) == 7 and parts[1:3] == ["api", "workflow"]
                    and parts[4] == "task" and parts[6] == "messages"):
                context = self._authentication_context()
                group_id, _ = self.consul.kv_get(
                    f"workflows/{parts[3]}/project-group"
                )
                capability = (
                    "task:message:interrupt"
                    if body.get("mode") == "interrupt" else "task:message:queue"
                )
                self._require(context, capability, group_id or None)
                return self._send_task_message(
                    parts[3], parts[5], body, actor=context.subject
                )

            if (path.startswith("/api/workflow/")
                    and path.endswith("/requirement-change/assessed")):
                req_id = path.split("/")[-3]
                return self._assessed_requirement_change(req_id, body)
            if path.startswith("/api/workflow/") and path.endswith("/control"):
                req_id = path.split("/")[-2]
                context = self._authentication_context()
                group_id, _ = self.consul.kv_get(
                    f"workflows/{req_id}/project-group"
                )
                self._require(context, "run:control", group_id or None)
                return self._control(req_id, body, actor=context.subject,
                                     group_id=group_id or UNASSIGNED_GROUP_ID)
            if path.startswith("/api/workflow/") and path.endswith("/messages"):
                req_id = path.split("/")[-2]
                return self._send_message(req_id, body)
            if path.startswith("/api/workflow/") and path.endswith("/proposals"):
                req_id = path.split("/")[-2]
                return self._confirm_proposal(req_id, body)
            if (len(parts) == 7 and parts[1:3] == ["api", "workflow"]
                    and parts[4] == "task"
                    and parts[6] in {"approve", "reject"}):
                return self._review_decision(
                    parts[3], parts[5], parts[6], body
                )
            if (len(parts) == 8 and parts[1:3] == ["api", "workflow"]
                    and parts[4] == "task" and parts[6] == "adaptive"):
                return self._adaptive_post(parts[3], parts[5], parts[7], body)
            self._send_json(404, {"error": "not found"})
        except APIError as e:
            self._handle_api_error(e)
        except AuthenticationError as e:
            self._send_error(401, "UNAUTHENTICATED", str(e))
        except Exception as e:
            log.exception("POST %s failed", self.path)
            self._send_error(500, "INTERNAL_ERROR", "服务器内部错误")

    def do_PATCH(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/")
        self.request_id = self.headers.get("X-Request-ID") or f"http_{uuid.uuid4().hex}"
        try:
            body = self._read_json_body()
            parts = path.split("/")
            if len(parts) == 4 and parts[1:3] == ["api", "project-groups"]:
                context = self._authentication_context()
                group_id = unquote(parts[3])
                self._require(context, "group:manage", group_id)
                if "expected_revision" not in body:
                    raise APIError(
                        "EXPECTED_REVISION_REQUIRED", "expected_revision 不能为空", 422
                    )
                changes = {key: value for key, value in body.items()
                           if key != "expected_revision"}
                group = self.project_groups.update(
                    group_id, expected_revision=int(body["expected_revision"]),
                    changes=changes,
                )
                return self._send_json(200, {"project_group": group})
            if len(parts) == 4 and parts[1:3] == ["api", "workspaces"]:
                context = self._authentication_context()
                workspace_id = unquote(parts[3])
                workspace = self.workspace_manager.get(workspace_id)
                self._require(context, "workspace:register", workspace["group_id"])
                if "expected_revision" not in body:
                    raise APIError("EXPECTED_REVISION_REQUIRED", "expected_revision 不能为空", 422)
                changes = {key: value for key, value in body.items() if key != "expected_revision"}
                updated = self.workspace_manager.update(
                    workspace_id, expected_revision=int(body["expected_revision"]), changes=changes
                )
                return self._send_json(200, {"workspace": updated})
            self._send_error(404, "NOT_FOUND", "接口不存在")
        except APIError as error:
            self._handle_api_error(error)
        except AuthenticationError as error:
            self._send_error(401, "UNAUTHENTICATED", str(error))
        except (TypeError, ValueError) as error:
            self._send_error(422, "VALIDATION_FAILED", str(error))
        except Exception:
            log.exception("PATCH %s failed", self.path)
            self._send_error(500, "INTERNAL_ERROR", "服务器内部错误")

    def do_PUT(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/")
        self.request_id = self.headers.get("X-Request-ID") or f"http_{uuid.uuid4().hex}"
        try:
            body = self._read_json_body()
            parts = path.split("/")
            if (len(parts) == 5 and parts[1:3] == ["api", "workspaces"]
                    and parts[4] == "file"):
                context = self._authentication_context()
                workspace_id = unquote(parts[3])
                values = {key: body.get(key) for key in (
                    "req_id", "run_id", "task_id", "attempt_id"
                )}
                missing = [key for key, value in values.items() if not value]
                if missing:
                    raise APIError(
                        "WORKSPACE_CONTEXT_REQUIRED", "Workspace 请求缺少 Attempt 上下文",
                        422, {"fields": missing},
                    )
                run_workspace = self.workspace_manager.get_run_workspace(
                    values["req_id"], values["run_id"], include_root=True
                )
                project_id = run_workspace.get("project_workspace_id")
                group_id = self.workspace_manager.get(project_id)["group_id"] if project_id else None
                self._require(context, "file:write", group_id)
                result = self.workspace_files.write_file(
                    workspace_id=workspace_id, **values, path=body.get("path", ""),
                    content=body.get("content"),
                    expected_sha256=body.get("expected_sha256"),
                    idempotency_key=self.headers.get("Idempotency-Key") or "",
                    actor=context.subject, reason=str(body.get("reason", "")),
                )
                return self._send_json(200, result)
            if (len(parts) == 6 and parts[1:3] == ["api", "project-groups"]
                    and parts[4] == "members"):
                context = self._authentication_context()
                group_id, subject_id = unquote(parts[3]), unquote(parts[5])
                self._require(context, "member:manage", group_id)
                member = self.project_groups.put_member(
                    group_id, subject_id, str(body.get("role", ""))
                )
                return self._send_json(200, {"member": member})
            self._send_error(404, "NOT_FOUND", "接口不存在")
        except APIError as error:
            self._handle_api_error(error)
        except AuthenticationError as error:
            self._send_error(401, "UNAUTHENTICATED", str(error))
        except Exception:
            log.exception("PUT %s failed", self.path)
            self._send_error(500, "INTERNAL_ERROR", "服务器内部错误")

    def do_DELETE(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/")
        self.request_id = self.headers.get("X-Request-ID") or f"http_{uuid.uuid4().hex}"
        try:
            parts = path.split("/")
            if (len(parts) == 6 and parts[1:3] == ["api", "project-groups"]
                    and parts[4] == "workflow-references"):
                context = self._authentication_context()
                group_id, req_id = unquote(parts[3]), unquote(parts[5])
                self._require(context, "group:manage", group_id)
                self.project_groups.delete_reference(group_id, req_id)
                return self._send_json(200, {"ok": True})
            if (len(parts) == 6 and parts[1:3] == ["api", "project-groups"]
                    and parts[4] == "members"):
                context = self._authentication_context()
                group_id, subject_id = unquote(parts[3]), unquote(parts[5])
                self._require(context, "member:manage", group_id)
                self.project_groups.delete_member(group_id, subject_id)
                return self._send_json(200, {"ok": True})
            self._send_error(404, "NOT_FOUND", "接口不存在")
        except APIError as error:
            self._handle_api_error(error)
        except AuthenticationError as error:
            self._send_error(401, "UNAUTHENTICATED", str(error))
        except Exception:
            log.exception("DELETE %s failed", self.path)
            self._send_error(500, "INTERNAL_ERROR", "服务器内部错误")

    def _list_workflows(self, context=None):
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
                t["/".join(parts[4:])] = it.get("_decoded", "")
            elif len(parts) == 3 and parts[2] == "control":
                w["control"] = it.get("_decoded", "")
            elif len(parts) == 3 and parts[2] == "title":
                w["title"] = it.get("_decoded", "")

        result = []
        for req_id, w in wfs.items():
            if context is not None and context.mode != "local":
                group_id, _ = self.consul.kv_get(
                    f"workflows/{req_id}/project-group"
                )
                if "group:read" not in self.authorization.capabilities_for(
                    context, group_id or None, req_id
                ):
                    continue
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
                tasks.setdefault(parts[1], {})["/".join(parts[2:])] = it.get("_decoded", "")
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

    def _get_session_events(self, req_id: str, task_name: str, parsed_url=None):
        items, _ = self.consul.kv_get(
            f"workflows/{req_id}/sessions/{task_name}/", recurse=True
        )
        if not items:
            return self._send_json(200, {
                "req_id": req_id, "task": task_name, "events": [],
                "sessions": [], "total": 0, "next_cursor": None,
            })

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

        # 扁平化、标准化为前端时间线事件。
        result: list[dict] = []
        for sid_data in sessions.values():
            evts = list(sid_data["events"].values())
            evts.sort(key=lambda e: str(e.get("seq", "")))
            result.extend(
                _normalize_session_event(event, sid_data["session_id"])
                for event in evts
            )

        result = [event for event in result if event is not None]
        result.sort(key=lambda event: (str(event.get("ts", "")), str(event.get("seq", ""))))
        result = _coalesce_timeline_events(result)

        query = parse_qs(parsed_url.query) if parsed_url is not None else {}
        requested_run = query.get("run_id", [None])[0]
        requested_attempt = query.get("attempt_id", [None])[0]
        if requested_run or requested_attempt:
            result = [event for event in result
                      if (not requested_run or event.get("run_id") == requested_run)
                      and (not requested_attempt or event.get("attempt_id") == requested_attempt)]
        try:
            limit = max(1, min(int(query.get("limit", ["200"])[0]), 500))
            cursor_raw = query.get("cursor", [""])[0]
            start = int(cursor_raw) if cursor_raw else max(0, len(result) - limit)
        except (TypeError, ValueError):
            return self._send_json(400, {"error": "cursor and limit must be integers"})
        start = max(0, min(start, len(result)))
        page = result[start:start + limit]
        next_cursor = start + len(page)
        if next_cursor >= len(result):
            next_cursor = None

        self._send_json(200, {
            "req_id": req_id,
            "task": task_name,
            "events": page,
            "sessions": [{"session_id": s["session_id"], "event_count": len(s["events"])}
                         for s in sessions.values()],
            "total": len(result),
            "next_cursor": next_cursor,
        })

    def _get_task_messages(self, req_id: str, task_name: str):
        self._send_json(200, {
            "req_id": req_id,
            "task": task_name,
            "messages": list_human_messages(self.consul, req_id, task_name),
        })

    def _send_task_message(
        self, req_id: str, task_name: str, body: dict, *, actor: str
    ):
        base = f"workflows/{req_id}/tasks/{task_name}"
        status, _ = self.consul.kv_get(f"{base}/status")
        if status is None:
            return self._send_json(404, {"error": "task not found"})
        item = None
        try:
            item = create_human_message(
                self.consul, req_id, task_name,
                message=body.get("message", ""),
                actor=actor,
                mode=body.get("mode", "queue"),
            )
            reopened = False
            if status in {"DONE", "FAILED", "AWAITING_REVIEW", "WAITING_FOR_HUMAN"}:
                rewind_to_task(
                    self.consul, req_id, task_name, task_name,
                    {
                        "source": "human",
                        "message_id": item["message_id"],
                        "comment": item["message"],
                    },
                    actor=item["actor"], allowed_targets=[task_name],
                    run_manager=self.run_manager,
                )
                self.consul.kv_delete(f"{base}/control")
                reopened = True
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            if item is not None:
                finish_human_message(
                    self.consul, req_id, task_name, item,
                    status="FAILED", error=str(exc),
                )
            return self._send_json(400, {"error": str(exc)})
        group_id, _ = self.consul.kv_get(f"workflows/{req_id}/project-group")
        event = self._append_event(
            "HUMAN_MESSAGE_CREATED",
            subject={"group_id": group_id or UNASSIGNED_GROUP_ID,
                     "req_id": req_id, "task_id": task_name},
            actor={"type": "human", "id": actor},
            data={"message_id": item["message_id"], "mode": item["mode"],
                  "reopened": reopened},
        )
        self._send_json(202, {"ok": True, "message": item, "reopened": reopened,
                              "event_id": event.event_id})

    def _list_agents(self):
        services = self.consul.list_services("agent-worker")
        agents = []
        for svc in services:
            s = svc.get("Service", {})
            checks = svc.get("Checks", [])
            healthy = all(c.get("Status") == "passing" for c in checks)
            agents.append({
                "agent_id": s.get("ID"),
                "agent_name": s.get("Meta", {}).get("agent_name", ""),
                "tags": s.get("Tags", []),
                "meta": s.get("Meta", {}),
                "healthy": healthy,
            })
        # ACP agents are task-scoped child processes, not registered services.
        # Project their active task records into the same dashboard response.
        items, _ = self.consul.kv_get("workflows/", recurse=True)
        active: dict[tuple[str, str], dict] = {}
        for item in items or []:
            parts = item["Key"].split("/")
            if len(parts) < 5 or parts[2] != "tasks":
                continue
            key = (parts[1], parts[3])
            active.setdefault(key, {})["/".join(parts[4:])] = item.get("_decoded", "")
        for (req_id, task_name), meta in active.items():
            if meta.get("status") != "IN_PROGRESS" or meta.get("execution_transport") != "acp":
                continue
            agents.append({
                "agent_id": meta.get("assigned_agent", ""),
                "agent_name": meta.get("acp/provider", ""),
                "tags": ["acp", "task-scoped"],
                "meta": {"req_id": req_id, "task_name": task_name,
                         "session_id": meta.get("acp/session_id", "")},
                "healthy": True,
            })
        self._send_json(200, {"agents": agents})

    def _control(self, req_id: str, body: dict, *, actor: str = "webapi",
                 group_id: str = UNASSIGNED_GROUP_ID):
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
            run_id = self.run_manager.get_or_create_run(req_id, actor)
            self.run_manager.record_transition(
                req_id, run_id, task,
                previous_state=prev_status or "",
                new_state="PENDING",
                actor=actor,
                reason="manual retry",
            )
        elif action == "ABORT":
            self.consul.kv_put(f"workflows/{req_id}/control", action)
            # 记录被 abort 的任务转换
            run_id = self.run_manager.get_or_create_run(req_id, actor)
            tasks_meta = self._load_tasks_for_abort(req_id)
            for name, meta in tasks_meta.items():
                if meta.get("status") in ("", "PENDING", "IN_PROGRESS", "BLOCKED",
                                           "AWAITING_REVIEW", "WAITING_FOR_HUMAN"):
                    prev = meta.get("status", "")
                    self.run_manager.record_transition(
                        req_id, run_id, name,
                        previous_state=prev,
                        new_state="ABORTED",
                        actor=actor,
                        reason="ABORT control signal via API",
                    )
                    self.consul.kv_put(
                        f"workflows/{req_id}/tasks/{name}/status", "ABORTED")
            self.run_manager.check_run_completion(req_id, run_id)
        else:
            self.consul.kv_put(f"workflows/{req_id}/control", action)

        from .adaptive_control import AdaptiveControlService
        AdaptiveControlService(self.consul, self.run_manager).record_event(
            req_id, body.get("task_name", "") or "__workflow__", "CONTROL_APPLIED",
            actor,
            {"action": action, "reason": body.get("reason", ""),
             "scope": f"task:{body.get('task_name')}" if body.get("task_name") else "workflow"},
        )
        self._append_event(
            "RUN_STATUS_CHANGED",
            subject={"group_id": group_id, "req_id": req_id,
                     **({"task_id": body.get("task_name", "")}
                        if body.get("task_name") else {})},
            actor={"type": "human", "id": actor},
            data={"action": action, "reason": body.get("reason", "")},
        )
        self._send_json(200, {"ok": True, "action": action, "req_id": req_id})

    def _assessed_requirement_change(self, req_id: str, body: dict):
        from .requirement_changes import RequirementChangeService
        try:
            result = RequirementChangeService(self.consul).apply_assessed(
                req_id, content=body.get("content", ""),
                reason=body.get("reason", ""),
                still_valid=body.get("still_valid", []),
                invalidated=body.get("invalidated", []),
                evidence=body.get("evidence", ""),
                actor=body.get("actor", ""),
            )
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            return self._send_json(400, {"error": str(exc)})
        return self._send_json(200, result)

    def _adaptive_get(self, req_id: str, task: str, action: str, parsed_url):
        from .adaptive_control import AdaptiveControlService
        service = AdaptiveControlService(self.consul, self.run_manager)
        if action == "next":
            query = parse_qs(parsed_url.query)
            actor = query.get("actor", [""])[0]
            action_type = query.get("type", ["EXECUTE"])[0]
            attempt_id = query.get("attempt_id", [""])[0]
            if not actor:
                return self._send_json(400, {"error": "actor is required"})
            return self._send_json(200, service.next_action(
                req_id, task, actor=actor, action_type=action_type,
                attempt_id=attempt_id,
            ))
        if action == "feedback":
            return self._send_json(200, {
                "feedback": service.list_feedback(req_id, task),
            })
        if action == "boundary":
            return self._send_json(200, service.boundary(req_id, task))
        return self._send_json(404, {"error": "unknown adaptive action"})

    def _adaptive_post(self, req_id: str, task: str, action: str, body: dict):
        from .adaptive_control import AdaptiveControlError, AdaptiveControlService
        service = AdaptiveControlService(self.consul, self.run_manager)
        try:
            if action == "check":
                result = service.submit_check(
                    req_id, task, action_id=body.get("action_id", ""),
                    state_version=body.get("state_version", -1),
                    verdict=body.get("verdict", ""), verifier=body.get("verifier", ""),
                    actor=body.get("actor", ""), evidence=body.get("evidence", {}),
                    command=body.get("command"), artifact_refs=body.get("artifact_refs"),
                    workspace_revision=body.get("workspace_revision", ""),
                )
            elif action == "route":
                result = service.submit_route(
                    req_id, task, target_task=body.get("target_task", ""),
                    reason=body.get("reason", ""), evidence=body.get("evidence", ""),
                    still_valid=body.get("still_valid", []),
                    invalidated=body.get("invalidated", []), actor=body.get("actor", ""),
                    failure_fingerprint=body.get("failure_fingerprint", ""),
                )
            elif action == "feedback":
                result = service.deliver_feedback(
                    req_id, task, message=body.get("message", ""),
                    actor=body.get("actor", ""), kind=body.get("kind", "message"),
                    source=body.get("source"),
                )
            elif action == "respond":
                result = service.respond_feedback(
                    req_id, task, feedback_id=body.get("feedback_id", ""),
                    decision=body.get("decision", ""),
                    understanding=body.get("understanding", ""),
                    reason=body.get("reason", ""), impact=body.get("impact", {}),
                    actor=body.get("actor", ""), question=body.get("question"),
                )
            elif action == "answer":
                result = service.answer_question(
                    req_id, task, answer=body.get("answer", ""),
                    actor=body.get("actor", ""),
                )
            elif action == "control":
                result = service.apply_control(
                    req_id, task=task, action=body.get("action", ""),
                    actor=body.get("actor", ""), reason=body.get("reason", ""),
                )
            else:
                return self._send_json(404, {"error": "unknown adaptive action"})
        except AdaptiveControlError as exc:
            return self._send_json(409, {
                "error": exc.code, "message": str(exc), "details": exc.details,
            })
        return self._send_json(200, result)

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
            self.consul.kv_put(f"{base}/validity", "VALID")
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
        policy_raw, _ = self.consul.kv_get(f"{base}/review_policy")
        try:
            policy = ReviewPolicy.from_dict(
                json.loads(policy_raw) if policy_raw else None
            )
            target_task = (
                body.get("recovery_target")
                or policy.default_recovery_target
                or task_name
            )
            allowed = policy.allowed_recovery_targets or [task_name]
            recovery = rewind_to_task(
                self.consul, req_id, task_name, target_task, feedback,
                actor=actor, allowed_targets=allowed,
                run_manager=self.run_manager,
            )
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            return self._send_json(400, {"error": str(exc)})
        return self._send_json(200, {
            "ok": True,
            "status": self.consul.kv_get(f"{base}/status")[0],
            "decision": decision,
            "recovery": recovery,
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


def _normalize_session_event(event: dict, session_id: str) -> dict | None:
    """Convert raw ACP updates and legacy events to the dashboard timeline schema."""
    if event.get("step_type"):
        return {
            **event,
            "ts": event.get("ts") or event.get("timestamp") or "",
            "level": event.get("level") or "info",
            "message": event.get("message") or event.get("type") or "Event",
            "session_id": session_id,
        }

    if event.get("type") != "ACP_UPDATE":
        return {
            "seq": event.get("seq", ""),
            "ts": event.get("timestamp", ""),
            "agent_id": event.get("agent_id", ""),
            "level": event.get("level", "info"),
            "step_type": event.get("type", "EVENT"),
            "message": event.get("message", event.get("type", "Event")),
            "data": event.get("data", {}),
            "session_id": session_id,
            "run_id": event.get("run_id", ""),
            "attempt_id": event.get("attempt_id", ""),
        }

    payload = event.get("payload", {})
    update = payload.get("update", {}) if isinstance(payload, dict) else {}
    update_type = update.get("sessionUpdate", "ACP_UPDATE")
    if update_type in {"available_commands_update", "current_mode_update"}:
        return None

    step_type = {
        "agent_message_chunk": "ASSISTANT_MSG",
        "tool_call": "TOOL_CALL",
        "tool_call_update": "TOOL_RESULT",
        "plan": "PLAN",
        "usage_update": "USAGE",
    }.get(update_type, "ACP_UPDATE")
    level = "error" if update.get("status") == "failed" else "info"
    message = _event_message(update_type, update)
    return {
        "seq": event.get("seq", ""),
        "ts": event.get("timestamp", ""),
        "agent_id": event.get("provider", ""),
        "level": level,
        "step_type": step_type,
        "message": message,
        "data": {
            "tool_call_id": update.get("toolCallId", ""),
            "status": update.get("status", ""),
            "kind": update.get("kind", ""),
        },
        "session_id": session_id,
        "run_id": event.get("run_id", ""),
        "attempt_id": event.get("attempt_id", ""),
    }


def _event_message(update_type: str, update: dict) -> str:
    content = update.get("content")
    if isinstance(content, dict) and isinstance(content.get("text"), str):
        return content["text"]
    if isinstance(content, list):
        chunks = [
            item.get("text", "") for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        if chunks:
            return "".join(chunks)
    for key in ("title", "message", "status"):
        if isinstance(update.get(key), str) and update[key]:
            return update[key]
    return update_type.replace("_", " ")


def _coalesce_timeline_events(events: list[dict]) -> list[dict]:
    """Join adjacent assistant chunks so one response is readable as one event."""
    result: list[dict] = []
    for event in events:
        if (result and event.get("step_type") == "ASSISTANT_MSG"
                and result[-1].get("step_type") == "ASSISTANT_MSG"
                and result[-1].get("session_id") == event.get("session_id")):
            result[-1]["message"] += event.get("message", "")
            result[-1]["seq"] = event.get("seq", result[-1].get("seq", ""))
            continue
        result.append(event)
    return result


def serve(consul: KVStore, host: str = "0.0.0.0", port: int = 8080,
          run_manager: RunManager = None, auth_config: AuthConfig | None = None,
          features: FeatureConfig | None = None,
          workspace_security: WorkspaceSecurity | None = None,
          workspace_manager: WorkspaceManager | None = None,
          sse_connection_seconds: float = 300.0) -> ThreadingHTTPServer:
    APIHandler.consul = consul
    APIHandler.message_bus = MessageBus(consul)
    APIHandler.run_manager = run_manager or RunManager(consul)
    APIHandler.auth_config = auth_config or AuthConfig()
    APIHandler.authorization = AuthorizationService(consul, APIHandler.auth_config)
    APIHandler.capabilities = CapabilitiesService(
        APIHandler.authorization, features=features
    )
    APIHandler.project_groups = ProjectGroupService(consul)
    APIHandler.workspace_manager = workspace_manager or WorkspaceManager(
        consul, workspace_security or WorkspaceSecurity({"default": "."}),
        APIHandler.project_groups,
    )
    APIHandler.event_journal = EventJournal(consul)
    APIHandler.sse_connection_seconds = sse_connection_seconds
    APIHandler.workspace_files = WorkspaceFileService(
        consul, APIHandler.workspace_manager, APIHandler.event_journal
    )
    APIHandler.workspace_merge = WorkspaceMergeService(
        consul, APIHandler.workspace_manager, APIHandler.event_journal
    )
    server = ThreadingHTTPServer((host, port), APIHandler)
    log.info("WebAPI serving on http://%s:%d/", host, port)
    return server
