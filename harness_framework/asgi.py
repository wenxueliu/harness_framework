"""ASGI entry point for the Dashboard API.

The existing handler remains available for CLI compatibility, while new
deployments can run the same API contract under an ASGI server. JSON requests
are adapted to the established APIHandler, and SSE is emitted natively so a
slow browser does not occupy a stdlib HTTP worker thread.
"""
from __future__ import annotations

import asyncio
import io
import json
import uuid
from email.message import Message
from urllib.parse import parse_qs, unquote, urlparse
from typing import Any

from .auth import AuthConfig, AuthenticationError, AuthorizationService, authenticate_request
from .capabilities import CapabilitiesService, FeatureConfig
from .event_journal import EventJournal
from .kv_store_protocol import KVStore
from .message_bus import MessageBus
from .project_groups import UNASSIGNED_GROUP_ID, ProjectGroupService
from .run_manager import RunManager
from .webapi import APIHandler
from .workspace_files import WorkspaceFileService
from .workspace_manager import WorkspaceManager
from .workspace_merge import WorkspaceMergeService
from .workspace_security import WorkspaceSecurity


class _ASGIHandler(APIHandler):
    def __init__(self, method: str, target: str, headers: Message, body: bytes, peer: str):
        self.command = method
        self.path = target
        self.headers = headers
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.client_address = (peer, 0)
        self.request_version = "HTTP/1.1"
        self.requestline = f"{method} {target} HTTP/1.1"
        self.close_connection = True
        self._status = 500
        self._response_headers: list[tuple[str, str]] = []

    def send_response(self, code: int, message: str | None = None) -> None:
        self._status = code

    def send_header(self, keyword: str, value: str) -> None:
        self._response_headers.append((keyword, value))

    def end_headers(self) -> None:
        return None

    def log_request(self, *args, **kwargs) -> None:
        return None


def _configure(
    consul: KVStore, *, run_manager: RunManager | None = None,
    auth_config: AuthConfig | None = None, features: FeatureConfig | None = None,
    workspace_security: WorkspaceSecurity | None = None,
    workspace_manager: WorkspaceManager | None = None,
) -> None:
    APIHandler.consul = consul
    APIHandler.message_bus = MessageBus(consul)
    APIHandler.run_manager = run_manager or RunManager(consul)
    APIHandler.auth_config = auth_config or AuthConfig()
    APIHandler.authorization = AuthorizationService(consul, APIHandler.auth_config)
    APIHandler.capabilities = CapabilitiesService(APIHandler.authorization, features=features)
    APIHandler.project_groups = ProjectGroupService(consul)
    APIHandler.workspace_manager = workspace_manager or WorkspaceManager(
        consul, workspace_security or WorkspaceSecurity({"default": "."}), APIHandler.project_groups
    )
    APIHandler.event_journal = EventJournal(consul)
    APIHandler.workspace_files = WorkspaceFileService(
        consul, APIHandler.workspace_manager, APIHandler.event_journal
    )
    APIHandler.workspace_merge = WorkspaceMergeService(
        consul, APIHandler.workspace_manager, APIHandler.event_journal
    )


def create_asgi_app(
    consul: KVStore, *, run_manager: RunManager | None = None,
    auth_config: AuthConfig | None = None, features: FeatureConfig | None = None,
    workspace_security: WorkspaceSecurity | None = None,
    workspace_manager: WorkspaceManager | None = None,
):
    _configure(consul, run_manager=run_manager, auth_config=auth_config,
               features=features, workspace_security=workspace_security,
               workspace_manager=workspace_manager)

    async def app(scope, receive, send):
        if scope.get("type") != "http":
            await send({"type": "http.response.start", "status": 400,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"http only"})
            return
        target = scope.get("raw_path", b"/").decode("utf-8", "replace")
        query = scope.get("query_string", b"").decode("utf-8", "replace")
        if query:
            target += "?" + query
        headers = Message()
        for key, value in scope.get("headers", []):
            headers[key.decode("latin1")] = value.decode("latin1")
        if urlparse(target).path.rstrip("/") == "/api/events":
            await _sse(scope, receive, send, target, headers)
            return
        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                continue
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        if "Content-Length" not in headers:
            headers["Content-Length"] = str(sum(len(chunk) for chunk in chunks))
        handler = _ASGIHandler(
            scope.get("method", "GET"), target, headers, b"".join(chunks),
            (scope.get("client") or ["127.0.0.1"])[0],
        )
        getattr(handler, "do_" + scope.get("method", "GET"), handler.do_OPTIONS)()
        response_headers = [(key.lower().encode(), value.encode())
                            for key, value in handler._response_headers]
        body = handler.wfile.getvalue()
        await send({"type": "http.response.start", "status": handler._status,
                    "headers": response_headers})
        await send({"type": "http.response.body", "body": body})

    async def _sse(scope, receive, send, target: str, headers: Message):
        handler = _ASGIHandler("GET", target, headers, b"",
                               (scope.get("client") or ["127.0.0.1"])[0])
        query = parse_qs(urlparse(target).query)
        try:
            context = handler._authentication_context()
        except AuthenticationError as exc:
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": json.dumps({"error": str(exc)}).encode()})
            return
        filters = {key: query.get(key, [None])[0]
                   for key in ("group_id", "req_id", "run_id", "task_id")}
        if not filters["group_id"] and filters["req_id"]:
            filters["group_id"], _ = handler.consul.kv_get(
                f"workflows/{filters['req_id']}/project-group"
            )
            filters["group_id"] = filters["group_id"] or UNASSIGNED_GROUP_ID
        try:
            if context.mode != "local" and not filters["group_id"]:
                raise AuthenticationError("事件订阅必须指定项目组或 Workflow")
            if filters["group_id"]:
                handler._require(context, "group:read", filters["group_id"], filters["req_id"])
        except Exception as exc:
            await send({"type": "http.response.start", "status": 403,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": json.dumps({"error": str(exc)}).encode()})
            return
        last_event_id = headers.get("Last-Event-ID")
        await send({"type": "http.response.start", "status": 200, "headers": [
            (b"content-type", b"text/event-stream; charset=utf-8"),
            (b"cache-control", b"no-cache, no-transform"),
            (b"connection", b"keep-alive"), (b"x-accel-buffering", b"no"),
        ]})
        for _ in range(120):
            events, reset = handler.event_journal.replay(after_event_id=last_event_id, limit=500)
            if reset:
                payload = json.dumps({"reason": "EVENT_CURSOR_EXPIRED"}).encode()
                await send({"type": "http.response.body",
                            "body": b"event: STREAM_RESET_REQUIRED\ndata: " + payload + b"\n\n",
                            "more_body": False})
                return
            chunks = []
            for event in events:
                last_event_id = event.event_id
                if any(value and event.subject.get(key) != value
                       for key, value in filters.items()):
                    continue
                payload = json.dumps(event.to_dict(), ensure_ascii=False).encode()
                chunks.append(b"id: " + event.event_id.encode() + b"\nevent: " +
                              event.type.encode() + b"\ndata: " + payload + b"\n\n")
            if not chunks:
                chunks = [b": heartbeat\n\n"]
            await send({"type": "http.response.body", "body": b"".join(chunks), "more_body": True})
            await asyncio.sleep(15)
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    return app
