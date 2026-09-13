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
from harness_framework.run_manager import RunManager
from harness_framework.auth import AuthConfig, AuthorizationService
from harness_framework.capabilities import CapabilitiesService, FeatureConfig
from harness_framework.project_groups import ProjectGroupService
from harness_framework.workspace_manager import WorkspaceManager
from harness_framework.workspace_security import WorkspaceSecurity
from harness_framework.workspace_files import WorkspaceFileService


def make_mock_run_manager():
    rm = MagicMock()
    rm.get_or_create_run.return_value = "run-test001"
    rm.list_runs.return_value = []
    rm.get_run.return_value = None
    rm.get_transitions.return_value = []
    rm.get_run_sessions.return_value = []
    rm.export_run_sessions.return_value = {}
    return rm


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

    def kv_list(prefix: str, cursor: str | None = None, limit: int = 100):
        from harness_framework.kv_pagination import paginate_items
        return paginate_items([
            {"key": key, "value": value, "modify_index": 1}
            for key, value in actual_store.items() if key.startswith(prefix)
        ], prefix=prefix, cursor=cursor, limit=limit)

    consul = MagicMock()
    consul.kv_get = Mock(side_effect=kv_get)
    consul.kv_put = Mock(side_effect=kv_put)
    consul.kv_delete = Mock(side_effect=kv_delete)
    consul.kv_list = Mock(side_effect=kv_list)
    consul.list_services = Mock(return_value=[])
    consul._store = actual_store
    return consul


def make_handler(store: dict, workspace_root: str = ".", real_run_manager: bool = False) -> tuple[APIHandler, MagicMock]:
    consul = make_consul_mock(store)

    from harness_framework.message_bus import MessageBus
    message_bus = MessageBus(consul)

    class TestHandler(APIHandler):
        pass

    TestHandler.consul = consul
    TestHandler.message_bus = message_bus
    TestHandler.run_manager = RunManager(consul) if real_run_manager else make_mock_run_manager()
    TestHandler.auth_config = AuthConfig(mode="local", local_user="test-user")
    TestHandler.authorization = AuthorizationService(consul, TestHandler.auth_config)
    TestHandler.capabilities = CapabilitiesService(
        TestHandler.authorization,
        FeatureConfig({"project_groups": False, "sse_events": True}),
    )
    TestHandler.project_groups = ProjectGroupService(consul)
    TestHandler.workspace_manager = WorkspaceManager(
        consul, WorkspaceSecurity({"test": workspace_root}),
        TestHandler.project_groups,
    )
    TestHandler.workspace_files = WorkspaceFileService(
        consul, TestHandler.workspace_manager
    )

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
    request_headers = {"Content-Length": str(len(body)), **(headers or {})}
    mock_headers.get = Mock(
        side_effect=lambda name, default=None: request_headers.get(name, default)
    )
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
    elif method == "PUT":
        handler.do_PUT()
    elif method == "PATCH":
        handler.do_PATCH()
    elif method == "DELETE":
        handler.do_DELETE()
    elif method == "OPTIONS":
        handler.do_OPTIONS()

    body_bytes = response_body.getvalue()
    try:
        payload = json.loads(body_bytes.decode())
    except json.JSONDecodeError:
        payload = {}
    return {"code": response_code[0], "body": payload}


class TestWebAPI:
    def test_workspace_first_run_requires_selection_and_is_idempotent(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        store = {
            "workflows/req-1/dependencies": "{}",
            "workflows/req-1/published": "true",
        }
        handler, _, _, _ = make_handler(store, str(tmp_path), real_run_manager=True)
        missing = call_do_method(
            handler, "POST", "/api/workflows/req-1/runs", b"{}",
            headers={"Idempotency-Key": "missing"},
        )
        assert missing["code"] == 422
        assert missing["body"]["error"]["code"] == "WORKSPACE_SELECTION_REQUIRED"

        group = call_do_method(
            handler, "POST", "/api/project-groups",
            json.dumps({"name": "Core"}).encode(),
        )["body"]["project_group"]
        handler.project_groups.assign_primary_workflow(group["group_id"], "req-1")
        workspace = call_do_method(
            handler, "POST", f"/api/project-groups/{group['group_id']}/workspaces",
            json.dumps({
                "name": "Repo", "root_alias": "test", "relative_path": "repo"
            }).encode(),
        )["body"]["workspace"]
        body = json.dumps({"workspace": {
            "project_workspace_id": workspace["workspace_id"],
            "strategy": "ORIGINAL",
        }}).encode()
        first = call_do_method(
            handler, "POST", "/api/workflows/req-1/runs", body,
            headers={"Idempotency-Key": "run-one"},
        )
        second = call_do_method(
            handler, "POST", "/api/workflows/req-1/runs", body,
            headers={"Idempotency-Key": "run-one"},
        )
        assert first["code"] == 201
        assert first["body"]["run"]["status"] == "RUNNING"
        assert second["code"] == 200
        assert second["body"]["run"]["run_id"] == first["body"]["run"]["run_id"]

    def test_project_workspace_register_list_and_preflight(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        handler, _, _, _ = make_handler({}, str(tmp_path))
        created_group = call_do_method(
            handler, "POST", "/api/project-groups",
            json.dumps({"name": "Core"}).encode(),
        )
        group_id = created_group["body"]["project_group"]["group_id"]
        created = call_do_method(
            handler, "POST", f"/api/project-groups/{group_id}/workspaces",
            json.dumps({
                "name": "Repo", "root_alias": "test", "relative_path": "repo"
            }).encode(),
        )
        assert created["code"] == 201
        workspace_id = created["body"]["workspace"]["workspace_id"]
        assert created["body"]["workspace"]["root_ref"] == "test:repo"
        listed = call_do_method(
            handler, "GET", f"/api/project-groups/{group_id}/workspaces"
        )
        assert listed["body"]["workspaces"][0]["workspace_id"] == workspace_id
        preflight = call_do_method(
            handler, "POST", f"/api/workspaces/{workspace_id}/preflight", b"{}"
        )
        assert preflight["code"] == 200
        assert preflight["body"]["preflight"]["exists"] is True

    def test_project_group_crud_uses_authenticated_actor(self):
        handler, _, _, _ = make_handler({})
        created = call_do_method(
            handler, "POST", "/api/project-groups",
            json.dumps({"name": "Core", "description": "team", "actor": "forged"}).encode(),
        )
        assert created["code"] == 201
        group = created["body"]["project_group"]
        assert group["created_by"] == "local:test-user"

        listed = call_do_method(handler, "GET", "/api/project-groups")
        assert any(item["group_id"] == group["group_id"]
                   for item in listed["body"]["project_groups"])

        updated = call_do_method(
            handler, "PATCH", f"/api/project-groups/{group['group_id']}",
            json.dumps({"expected_revision": 1, "name": "Platform"}).encode(),
        )
        assert updated["code"] == 200
        assert updated["body"]["project_group"]["name"] == "Platform"

    def test_project_group_member_api_and_structured_conflict(self):
        handler, _, _, _ = make_handler({})
        created = call_do_method(
            handler, "POST", "/api/project-groups",
            json.dumps({"name": "Core"}).encode(),
        )
        group_id = created["body"]["project_group"]["group_id"]
        member = call_do_method(
            handler, "PUT", f"/api/project-groups/{group_id}/members/user%3Abob",
            json.dumps({"role": "developer"}).encode(),
        )
        assert member["code"] == 200
        assert member["body"]["member"]["role"] == "DEVELOPER"

        conflict = call_do_method(
            handler, "DELETE", f"/api/project-groups/{group_id}/members/local%3Atest-user",
        )
        assert conflict["code"] == 409
        assert conflict["body"]["error"]["code"] == "LAST_OWNER_REQUIRED"

    def test_capabilities_reports_real_features_permissions_and_actor(self):
        handler, _, _, _ = make_handler({})
        response = call_do_method(handler, "GET", "/api/capabilities")

        assert response["code"] == 200
        assert response["body"]["features"]["sse_events"] is True
        assert response["body"]["features"]["project_groups"] is False
        assert "group:manage" in response["body"]["permissions"]
        assert response["body"]["actor"]["subject"] == "local:test-user"
        assert response["body"]["mode"] == "local"
        assert response["body"]["request_id"].startswith("http_")

    def test_assessed_requirement_change_http_endpoint(self):
        from harness_framework.versioning import VersionedResourceStore
        store = {
            "workflows/req-001/dependencies": json.dumps({
                "design": {"depends_on": []},
                "backend": {"depends_on": ["design"]},
            }),
            "workflows/req-001/tasks/design/status": "DONE",
            "workflows/req-001/tasks/backend/status": "DONE",
        }
        handler, consul, _, _ = make_handler(store)
        versions = VersionedResourceStore(consul)
        versions.publish("req-001", "requirement", {"content": "v1"}, actor="setup")
        versions.publish("req-001", "dag", {
            "design": {"depends_on": []}, "backend": {"depends_on": ["design"]},
        }, actor="setup")
        response = call_do_method(
            handler, "POST", "/api/workflow/req-001/requirement-change/assessed",
            json.dumps({
                "content": "v2", "reason": "backend change",
                "still_valid": ["design"], "invalidated": ["backend"],
                "evidence": "contract changed", "actor": "alice",
            }).encode(),
        )
        assert response["code"] == 200
        assert response["body"]["impact_assessment"]["changed_roots"] == ["backend"]

    def test_adaptive_http_feedback_boundary_and_response(self):
        store = {
            "workflows/req-001/dependencies": json.dumps({
                "backend": {"depends_on": []},
            }),
            "workflows/req-001/tasks/backend/status": "IN_PROGRESS",
        }
        handler, consul, _, _ = make_handler(store)
        delivered = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/task/backend/adaptive/feedback",
            json.dumps({"message": "check compatibility", "actor": "alice"}).encode(),
        )
        assert delivered["code"] == 200
        feedback_id = delivered["body"]["feedback_id"]

        next_result = call_do_method(
            handler, "GET",
            "/api/workflow/req-001/task/backend/adaptive/next?actor=agent-1",
        )
        assert next_result["body"]["type"] == "INTERPRET_FEEDBACK"
        assert next_result["body"]["feedback"]["status"] == "OBSERVED"

        response = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/task/backend/adaptive/respond",
            json.dumps({
                "feedback_id": feedback_id, "decision": "CONTINUE",
                "understanding": "review compatibility", "reason": "clear",
                "impact": {}, "actor": "agent-1",
            }).encode(),
        )
        assert response["code"] == 200
        assert response["body"]["status"] == "APPLIED"

        action = call_do_method(
            handler, "GET",
            "/api/workflow/req-001/task/backend/adaptive/next?actor=agent-1",
        )
        assert action["code"] == 200
        assert action["body"]["action_type"] == "EXECUTE"
        assert "workflows/req-001/tasks/backend/actions/current" in consul._store

    def test_adaptive_http_rejects_check_while_paused(self):
        store = {
            "workflows/req-001/dependencies": json.dumps({
                "backend": {"depends_on": []},
            }),
            "workflows/req-001/tasks/backend/status": "IN_PROGRESS",
            "workflows/req-001/tasks/backend/control": "PAUSE",
        }
        handler, _, _, _ = make_handler(store)
        response = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/task/backend/adaptive/check",
            json.dumps({
                "action_id": "old", "state_version": 1, "verdict": "PASS",
                "verifier": "agent", "actor": "agent-1", "evidence": {},
            }).encode(),
        )
        assert response["code"] == 409
        assert response["body"]["error"] == "E_BOUNDARY_BLOCKED"

    def test_human_approve_completes_awaiting_task(self):
        store = {
            "workflows/req-001/tasks/backend/status": "AWAITING_REVIEW",
        }
        handler, consul, _, _ = make_handler(store)
        body = json.dumps({"actor": "alice", "comment": "approved"}).encode()
        resp = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/task/backend/approve", body,
        )
        assert resp["code"] == 200
        assert consul._store["workflows/req-001/tasks/backend/status"] == "DONE"
        assert consul._store["workflows/req-001/tasks/backend/approved_by"] == "alice"

    def test_human_reject_returns_task_to_pending_with_feedback(self):
        store = {
            "workflows/req-001/dependencies": json.dumps({
                "backend": {"depends_on": []},
            }),
            "workflows/req-001/tasks/backend/status": "AWAITING_REVIEW",
            "workflows/req-001/tasks/backend/attempt_id": "attempt-old",
            "workflows/req-001/tasks/backend/evidence/review/verdict": "PASS",
        }
        handler, consul, _, _ = make_handler(store)
        body = json.dumps({"actor": "alice", "comment": "fix copy"}).encode()
        resp = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/task/backend/reject", body,
        )
        assert resp["code"] == 200
        assert consul._store["workflows/req-001/tasks/backend/status"] == "PENDING"
        feedback = json.loads(consul._store[
            "workflows/req-001/tasks/backend/recovery_feedback/current"
        ])
        assert feedback["feedback"]["comment"] == "fix copy"
        assert "workflows/req-001/tasks/backend/attempt_id" not in consul._store

    def test_human_reject_can_rewind_to_allowed_upstream(self):
        store = {
            "workflows/req-001/dependencies": json.dumps({
                "design": {"depends_on": []},
                "backend": {"depends_on": ["design"]},
            }),
            "workflows/req-001/tasks/design/status": "DONE",
            "workflows/req-001/tasks/backend/status": "AWAITING_REVIEW",
            "workflows/req-001/tasks/backend/review_policy": json.dumps({
                "allowed_recovery_targets": ["backend", "design"],
                "default_recovery_target": "backend",
            }),
        }
        handler, consul, _, _ = make_handler(store)
        body = json.dumps({
            "actor": "alice", "comment": "fix design",
            "recovery_target": "design",
        }).encode()
        resp = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/task/backend/reject", body,
        )
        assert resp["code"] == 200
        assert consul._store["workflows/req-001/tasks/design/status"] == "PENDING"
        assert consul._store["workflows/req-001/tasks/backend/status"] == "BLOCKED"

    def test_human_decision_requires_awaiting_review(self):
        store = {"workflows/req-001/tasks/backend/status": "IN_PROGRESS"}
        handler, _, _, _ = make_handler(store)
        body = json.dumps({"actor": "alice", "comment": "approved"}).encode()
        resp = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/task/backend/approve", body,
        )
        assert resp["code"] == 409

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
        assert "context" in resp["body"]

    def test_get_workflow_not_found(self):
        handler, _, _, _ = make_handler({})

        resp = call_do_method(handler, "GET", "/api/workflow/not-exist")

        assert resp["code"] == 404
        assert "error" in resp["body"]

    def test_session_route_normalizes_acp_events(self):
        event = {
            "timestamp": "2026-09-06T01:02:03Z",
            "type": "ACP_UPDATE",
            "provider": "claude",
            "payload": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "finished"},
                }
            },
        }
        store = {
            "workflows/req-001/sessions/backend/session-1/events/0001": json.dumps(event),
        }
        handler, _, _, _ = make_handler(store)

        resp = call_do_method(
            handler, "GET", "/api/sessions/req-001/backend?limit=100",
        )

        assert resp["code"] == 200
        assert resp["body"]["req_id"] == "req-001"
        assert resp["body"]["task"] == "backend"
        assert resp["body"]["events"][0]["step_type"] == "ASSISTANT_MSG"
        assert resp["body"]["events"][0]["message"] == "finished"

    def test_task_message_is_queued_for_running_task(self):
        store = {"workflows/req-001/tasks/backend/status": "IN_PROGRESS"}
        handler, consul, _, _ = make_handler(store)
        body = json.dumps({
            "actor": "alice", "message": "Use JWT", "mode": "queue",
        }).encode()

        sent = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/task/backend/messages", body,
        )
        listed = call_do_method(
            handler, "GET", "/api/workflow/req-001/task/backend/messages",
        )

        assert sent["code"] == 202
        assert sent["body"]["reopened"] is False
        assert listed["body"]["messages"][0]["message"] == "Use JWT"
        assert consul._store["workflows/req-001/tasks/backend/status"] == "IN_PROGRESS"

    def test_task_message_reopens_completed_task_and_downstream(self):
        store = {
            "workflows/req-001/dependencies": json.dumps({
                "backend": {"depends_on": []},
                "test": {"depends_on": ["backend"]},
            }),
            "workflows/req-001/tasks/backend/status": "DONE",
            "workflows/req-001/tasks/backend/validity": "VALID",
            "workflows/req-001/tasks/test/status": "DONE",
            "workflows/req-001/tasks/test/validity": "VALID",
        }
        handler, consul, _, _ = make_handler(store)
        body = json.dumps({
            "actor": "alice", "message": "Adjust the API", "mode": "queue",
        }).encode()

        resp = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/task/backend/messages", body,
        )

        assert resp["code"] == 202
        assert resp["body"]["reopened"] is True
        assert consul._store["workflows/req-001/tasks/backend/status"] == "PENDING"
        assert consul._store["workflows/req-001/tasks/test/status"] == "BLOCKED"
        assert consul._store["workflows/req-001/tasks/backend/validity"] == "INVALIDATED"

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
                    "Meta": {
                        "agent_name": "backend-agent",
                        "hostname": "dev-1",
                    }
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
        assert healthy["agent_name"] == "backend-agent"
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

    def test_get_proposals_empty(self):
        """没有提案时返回空列表"""
        store = {
            "workflows/req-001/status": "CONFIRMED",
            "workflows/req-001/dependencies": json.dumps({
                "design": {"type": "design"},
            }),
        }
        handler, _, _, _ = make_handler(store)

        resp = call_do_method(handler, "GET", "/api/workflow/req-001/proposals")

        assert resp["code"] == 200
        assert resp["body"]["req_id"] == "req-001"
        assert resp["body"]["status"] == "CONFIRMED"
        assert resp["body"]["proposals"] == []

    def test_get_proposals_with_pending(self):
        """有提案时返回提案列表"""
        store = {
            "workflows/req-001/status": "Proposal",
            "workflows/req-001/dependencies": json.dumps({
                "design": {"type": "design"},
                "perf-opt": {
                    "type": "task",
                    "depends_on": ["design"],
                    "proposed_by": "test",
                    "proposed_at": "2025-04-23T10:00:00Z",
                    "reason": "performance test failed",
                },
            }),
        }
        handler, _, _, _ = make_handler(store)

        resp = call_do_method(handler, "GET", "/api/workflow/req-001/proposals")

        assert resp["code"] == 200
        assert resp["body"]["status"] == "Proposal"
        assert len(resp["body"]["proposals"]) == 1
        assert resp["body"]["proposals"][0]["task_name"] == "perf-opt"

    def test_confirm_proposal(self):
        """确认提案"""
        store = {
            "workflows/req-001/status": "Proposal",
            "workflows/req-001/dependencies": json.dumps({
                "perf-opt": {"type": "task", "proposed_by": "test"},
            }),
        }
        handler, consul, _, _ = make_handler(store)

        resp = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/proposals",
            json.dumps({"action": "confirm"}).encode()
        )

        assert resp["code"] == 200
        assert resp["body"]["success"] is True
        assert resp["body"]["status"] == "CONFIRMED"

    def test_confirm_proposal_with_rejected_tasks(self):
        """确认时拒绝部分任务"""
        store = {
            "workflows/req-001/status": "Proposal",
            "workflows/req-001/dependencies": json.dumps({
                "perf-opt": {"type": "task", "proposed_by": "test"},
                "sec-fix": {"type": "task", "proposed_by": "test"},
            }),
        }
        handler, consul, _, _ = make_handler(store)

        resp = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/proposals",
            json.dumps({"action": "confirm", "rejected_tasks": ["sec-fix"]}).encode()
        )

        assert resp["code"] == 200

    def test_reject_proposal(self):
        """拒绝提案"""
        store = {
            "workflows/req-001/status": "Proposal",
            "workflows/req-001/dependencies": json.dumps({
                "perf-opt": {"type": "task", "proposed_by": "test"},
            }),
        }
        handler, consul, _, _ = make_handler(store)

        resp = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/proposals",
            json.dumps({"action": "reject"}).encode()
        )

        assert resp["code"] == 200
        assert resp["body"]["success"] is True
        assert resp["body"]["status"] == "CONFIRMED"

    def test_confirm_proposal_not_in_proposal_state(self):
        """非 Proposal 状态确认失败"""
        store = {
            "workflows/req-001/status": "CONFIRMED",
            "workflows/req-001/dependencies": json.dumps({}),
        }
        handler, _, _, _ = make_handler(store)

        resp = call_do_method(
            handler, "POST",
            "/api/workflow/req-001/proposals",
            json.dumps({"action": "confirm"}).encode()
        )

        assert resp["code"] == 400
