"""
WebAPI 单元测试

测试策略：直接调用 APIHandler 实例方法，避免 BaseHTTPRequestHandler 交互复杂性。
"""
from __future__ import annotations

import base64
import json
from io import BytesIO
from unittest.mock import MagicMock, Mock

import pytest

from harness_framework.webapi import APIHandler


def make_consul_mock(store: dict) -> MagicMock:
    """构建适配 WebAPI 逻辑的 mock ConsulClient。"""
    actual_store = dict(store)

    def kv_get(key: str, recurse: bool = False):
        if recurse:
            prefix = key.rstrip("/") + "/"
            matches = []
            for k, v in actual_store.items():
                if k.startswith(prefix):
                    matches.append({
                        "Key": k,
                        "Value": base64.b64encode(v.encode()).decode() if v else "",
                        "ModifyIndex": 1,
                        "_decoded": v,
                    })
            if matches:
                return matches, 1
            return None, 0
        v = actual_store.get(key)
        if v is not None:
            return v, 1
        return None, 0

    def kv_put(key: str, value: str, cas: int = None) -> bool:
        actual_store[key] = value
        return True

    def kv_delete(key: str, recurse: bool = False) -> None:
        if recurse:
            prefix = key.rstrip("/") + "/"
            to_del = [k for k in actual_store if k.startswith(prefix)]
            for k in to_del:
                del actual_store[k]
        elif key in actual_store:
            del actual_store[key]

    consul = MagicMock()
    consul.kv_get = Mock(side_effect=kv_get)
    consul.kv_put = Mock(side_effect=kv_put)
    consul.kv_delete = Mock(side_effect=kv_delete)
    consul.list_services = Mock(return_value=[])
    consul._store = actual_store
    return consul


def make_handler(store: dict) -> tuple[APIHandler, MagicMock]:
    consul = make_consul_mock(store)

    from harness_framework.message_bus import MessageBus
    message_bus = MessageBus(consul)

    class TestHandler(APIHandler):
        pass

    TestHandler.consul = consul
    TestHandler.message_bus = message_bus

    response_body = BytesIO()
    response_code = [200]
    response_headers = {}

    def mock_send_response(code):
        response_code[0] = code

    def mock_send_header(name, value):
        response_headers[name] = value

    def mock_end_headers():
        pass

    def mock_wfile_write(data):
        response_body.write(data)

    handler = TestHandler.__new__(TestHandler)
    handler.send_response = mock_send_response
    handler.send_header = mock_send_header
    handler.end_headers = mock_end_headers
    handler.wfile = MagicMock()
    handler.wfile.write = mock_wfile_write
    handler.rfile = BytesIO()
    handler.path = "/"
    handler.command = "GET"
    handler.headers = MagicMock()
    handler.headers.get = Mock(return_value="0")
    handler.log_message = Mock()
    handler.client_address = ("127.0.0.1", 8000)
    handler.server = MagicMock()
    handler.close_connection = False
    handler.connection = MagicMock()

    return handler, consul, response_code, response_body


def call_do_method(handler, method: str, path: str, body: bytes = b"", headers: dict | None = None):
    handler.command = method
    handler.path = path
    handler.rfile = BytesIO(body)
    mock_headers = MagicMock()
    mock_headers.get = Mock(return_value=str(len(body)))
    handler.headers = mock_headers

    response_body = BytesIO()
    response_code = [200]
    response_headers = {}

    def mock_send_response(code):
        response_code[0] = code
    def mock_send_header(name, value):
        response_headers[name] = value
    def mock_end_headers():
        pass

    handler.send_response = mock_send_response
    handler.send_header = mock_send_header
    handler.end_headers = mock_end_headers
    handler.wfile = MagicMock()
    handler.wfile.write = response_body.write
    handler.log_message = Mock()

    if method == "GET":
        handler.do_GET()
    elif method == "POST":
        handler.do_POST()
    elif method == "OPTIONS":
        handler.do_OPTIONS()

    body_bytes = response_body.getvalue()
    try:
        payload = json.loads(body_bytes.decode())
    except json.JSONDecodeError:
        payload = {}
    return {"code": response_code[0], "body": payload}


class TestWebAPI:
    def test_list_workflows_empty(self):
        handler, consul, _, _ = make_handler({})

        resp = call_do_method(handler, "GET", "/api/workflows")

        assert resp["code"] == 200
        assert resp["body"]["workflows"] == []

    def test_list_workflows_with_data(self):
        store = {
            "workflows/req-001/title": "登录功能",
            "workflows/req-001/tasks/design/status": "DONE",
            "workflows/req-001/tasks/backend/status": "IN_PROGRESS",
            "workflows/req-002/title": "注册功能",
            "workflows/req-002/tasks/design/status": "DONE",
            "workflows/req-002/tasks/backend/status": "DONE",
        }
        handler, _, _, _ = make_handler(store)

        resp = call_do_method(handler, "GET", "/api/workflows")

        assert resp["code"] == 200
        wfs = resp["body"]["workflows"]
        assert len(wfs) == 2
        req001 = next(w for w in wfs if w["req_id"] == "req-001")
        req002 = next(w for w in wfs if w["req_id"] == "req-002")
        assert req001["phase"] == "RUNNING"
        assert req001["progress"] == 50.0
        assert req002["phase"] == "DONE"
        assert req002["progress"] == 100.0

    def test_get_workflow(self):
        store = {
            "workflows/req-001/dependencies": '{"backend": {"type": "backend", "depends_on": []}}',
            "workflows/req-001/tasks/backend/status": "DONE",
            "workflows/req-001/tasks/backend/type": "backend",
            "workflows/req-001/feedback/login/status": "OPEN",
            "workflows/req-001/context/summary": "后端已完成",
            "workflows/req-001/control": "",
        }
        handler, _, _, _ = make_handler(store)

        resp = call_do_method(handler, "GET", "/api/workflow/req-001")

        assert resp["code"] == 200
        assert resp["body"]["req_id"] == "req-001"
        assert "backend" in resp["body"]["tasks"]
        assert "feedback" in resp["body"]
        assert "context" in resp["body"]

    def test_get_workflow_not_found(self):
        handler, _, _, _ = make_handler({})

        resp = call_do_method(handler, "GET", "/api/workflow/not-exist")

        assert resp["code"] == 404
        assert "error" in resp["body"]

    def test_control_pause(self):
        handler, consul, _, _ = make_handler({})

        resp = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/control",
            json.dumps({"action": "PAUSE"}).encode()
        )

        assert resp["code"] == 200
        assert resp["body"]["action"] == "PAUSE"
        assert consul.kv_put.called

    def test_control_resume(self):
        handler, consul, _, _ = make_handler({})

        resp = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/control",
            json.dumps({"action": "RESUME"}).encode()
        )

        assert resp["code"] == 200
        assert resp["body"]["action"] == "RESUME"
        assert consul.kv_delete.called

    def test_control_abort(self):
        store = {
            "workflows/req-001/dependencies": json.dumps({
                "design": {"type": "design"},
                "backend": {"type": "backend"},
            }),
            "workflows/req-001/tasks/design/status": "IN_PROGRESS",
            "workflows/req-001/tasks/backend/status": "PENDING",
        }
        handler, consul, _, _ = make_handler(store)

        resp = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/control",
            json.dumps({"action": "ABORT"}).encode()
        )

        assert resp["code"] == 200
        assert consul.kv_put.called

    def test_control_retry(self):
        handler, consul, _, _ = make_handler({})

        resp = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/control",
            json.dumps({"action": "RETRY", "task_name": "backend"}).encode()
        )

        assert resp["code"] == 200
        assert resp["body"]["action"] == "RETRY"
        pending_call = any(
            "backend" in str(c) and "PENDING" in str(c)
            for c in consul.kv_put.call_args_list
        )
        assert pending_call, "backend should be reset to PENDING"

    def test_control_retry_missing_task_name(self):
        handler, _, _, _ = make_handler({})

        resp = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/control",
            json.dumps({"action": "RETRY"}).encode()
        )

        assert resp["code"] == 400
        assert "error" in resp["body"]

    def test_control_invalid_action(self):
        handler, _, _, _ = make_handler({})

        resp = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/control",
            json.dumps({"action": "INVALID"}).encode()
        )

        assert resp["code"] == 400
        assert "invalid action" in resp["body"]["error"]

    def test_health_check(self):
        handler, _, _, _ = make_handler({})

        resp = call_do_method(handler, "GET", "/api/health")

        assert resp["code"] == 200
        assert resp["body"]["ok"] is True
        assert resp["body"]["service"] == "harness-framework"

    def test_list_agents_empty(self):
        handler, _, _, _ = make_handler({})

        resp = call_do_method(handler, "GET", "/api/agents")

        assert resp["code"] == 200
        assert resp["body"]["agents"] == []

    def test_list_agents_with_data(self):
        handler, consul, _, _ = make_handler({})
        consul.list_services = Mock(return_value=[
            {
                "Service": {
                    "ID": "agent-001",
                    "Tags": ["backend"],
                    "Meta": {"hostname": "dev-1"}
                },
                "Checks": [{"Status": "passing"}]
            },
            {
                "Service": {
                    "ID": "agent-002",
                    "Tags": [],
                    "Meta": {}
                },
                "Checks": [{"Status": "failing"}]
            },
        ])

        resp = call_do_method(handler, "GET", "/api/agents")

        assert resp["code"] == 200
        agents = resp["body"]["agents"]
        assert len(agents) == 2
        healthy = next(a for a in agents if a["agent_id"] == "agent-001")
        unhealthy = next(a for a in agents if a["agent_id"] == "agent-002")
        assert healthy["healthy"] is True
        assert unhealthy["healthy"] is False

    def test_not_found(self):
        handler, _, _, _ = make_handler({})

        resp = call_do_method(handler, "GET", "/api/unknown")

        assert resp["code"] == 404

    def test_options_cors(self):
        handler, _, _, _ = make_handler({})

        resp = call_do_method(handler, "OPTIONS", "/api/workflows")

        assert resp["code"] == 200

    def test_get_messages_empty(self):
        """获取空队列返回空列表"""
        handler, _, _, _ = make_handler({})

        resp = call_do_method(handler, "GET", "/api/workflow/req-001/messages/task-backend")

        assert resp["code"] == 200
        assert resp["body"]["messages"] == []

    def test_get_messages_with_data(self):
        """获取队列中的消息"""
        store = {
            "workflows/req-001/requests/task-backend/msg-001": json.dumps({
                "msg_id": "msg-001",
                "req_id": "req-001",
                "from": "task-frontend",
                "to": "task-backend",
                "action": "provide_api",
                "params": {"endpoint": "/api/user"},
                "status": "PENDING",
                "result": None,
                "created_at": "2025-04-22T10:00:00Z",
                "timeout": 300,
            }),
        }
        handler, _, _, _ = make_handler(store)

        resp = call_do_method(handler, "GET", "/api/workflow/req-001/messages/task-backend")

        assert resp["code"] == 200
        assert len(resp["body"]["messages"]) == 1
        assert resp["body"]["messages"][0]["action"] == "provide_api"

    def test_get_messages_with_status_filter(self):
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
        handler, _, _, _ = make_handler(store)

        resp = call_do_method(handler, "GET", "/api/workflow/req-001/messages/task-backend?status=PENDING")

        assert resp["code"] == 200
        assert len(resp["body"]["messages"]) == 1
        assert resp["body"]["messages"][0]["msg_id"] == "msg-001"

    def test_send_message(self):
        """发送消息"""
        handler, consul, _, _ = make_handler({})

        resp = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/messages",
            json.dumps({
                "from": "task-frontend",
                "to": "task-backend",
                "action": "provide_api",
                "params": {"endpoint": "/api/user"},
                "timeout": 600,
            }).encode()
        )

        assert resp["code"] == 200
        assert resp["body"]["ok"] is True
        assert "msg_id" in resp["body"]
        assert resp["body"]["message"]["action"] == "provide_api"
        assert resp["body"]["message"]["from"] == "task-frontend"

    def test_send_message_missing_fields(self):
        """缺少必需字段返回 400"""
        handler, _, _, _ = make_handler({})

        resp = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/messages",
            json.dumps({"from": "task-frontend"}).encode()
        )

        assert resp["code"] == 400
        assert "error" in resp["body"]
