"""Dashboard API integration checks.

These intentionally exercise a real ThreadingHTTPServer rather than calling
handler methods directly.  UI automation covers the browser contract; this
file covers the HTTP status/error/envelope and workspace binding contract.
"""
from __future__ import annotations

import http.client
import asyncio
import json
import subprocess
import threading
from pathlib import Path

import pytest

from harness_framework.auth import AuthConfig
from harness_framework.asgi import create_asgi_app
from harness_framework.capabilities import FeatureConfig
from harness_framework.event_journal import EventJournal
from harness_framework.project_groups import ProjectGroupService
from harness_framework.run_manager import RunManager
from harness_framework.webapi import serve
from harness_framework.workspace_files import WorkspaceFileService
from harness_framework.workspace_manager import WorkspaceManager
from harness_framework.workspace_security import WorkspaceSecurity

from tests.conftest import MockConsulStore


def _request(port: int, method: str, path: str, body: dict | None = None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    payload = json.dumps(body).encode() if body is not None else None
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    connection.request(method, path, body=payload, headers=request_headers)
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    return response.status, json.loads(raw or b"{}")


@pytest.mark.api
def test_dashboard_run_workspace_file_api(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run([
        "git", "-C", str(repo), "-c", "user.name=api", "-c",
        "user.email=api@example.invalid", "commit", "-qm", "initial",
    ], check=True)

    store = MockConsulStore({
        "workflows/api-smoke/dependencies": json.dumps({"task": {"type": "task", "depends_on": []}}),
        "workflows/api-smoke/title": "API smoke",
        "workflows/api-smoke/published": "true",
        "workflows/api-smoke/project-group": "grp_api",
    })
    groups = ProjectGroupService(store)
    group = groups.create(name="API", description="", actor="local:api")
    store.kv_put("workflows/api-smoke/project-group", group["group_id"])
    workspace_manager = WorkspaceManager(
        store, WorkspaceSecurity({"test": str(tmp_path)}), groups,
        provision_root_alias="test",
    )
    workspace = workspace_manager.register_local(
        group_id=group["group_id"], name="repo", root_alias="test",
        relative_path="repo", access="READ_WRITE",
    )
    run_manager = RunManager(store, workspace_manager)
    server = serve(
        store, host="127.0.0.1", port=0, run_manager=run_manager,
        auth_config=AuthConfig(mode="local", local_user="api"),
        features=FeatureConfig({
            "project_groups": True, "workspace_browse": True,
            "workspace_write": True, "workspace_diff": True, "sse_events": True,
        }), workspace_security=WorkspaceSecurity({"test": str(tmp_path)}),
        workspace_manager=workspace_manager,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        status, capabilities = _request(port, "GET", "/api/capabilities?group_id=" + group["group_id"])
        assert status == 200
        assert capabilities["features"]["workspace_write"] is True

        status, created = _request(
            port, "POST", "/api/workflows/api-smoke/runs",
            {"workspace": {"project_workspace_id": workspace["workspace_id"], "strategy": "CONTROLLED_COPY"}},
            {"Idempotency-Key": "api-smoke-run"},
        )
        assert status == 201
        run_id = created["run"]["run_id"]
        binding = workspace_manager.bind_attempt(
            req_id="api-smoke", run_id=run_id, task_id="task", attempt_id="attempt-1",
            write_scope=("README.md",),
        )
        query = "req_id=api-smoke&run_id=" + run_id + "&task_id=task&attempt_id=attempt-1"
        status, tree = _request(port, "GET", f"/api/workspaces/{binding['workspace_id']}/tree?{query}")
        assert status == 200
        assert any(entry["path"] == "README.md" for entry in tree["entries"])

        status, file_data = _request(port, "GET", f"/api/workspaces/{binding['workspace_id']}/file?{query}&path=README.md")
        assert status == 200
        assert file_data["content"] == "hello\n"
        status, saved = _request(
            port, "PUT", f"/api/workspaces/{binding['workspace_id']}/file",
            {**{key: binding[key] for key in ("req_id", "run_id", "task_id", "attempt_id")},
             "path": "README.md", "content": "updated\n", "expected_sha256": file_data["sha256"], "reason": "api smoke"},
            {"Idempotency-Key": "api-write-1"},
        )
        assert status == 200
        assert saved["sha256"] != file_data["sha256"]
        status, conflict = _request(
            port, "PUT", f"/api/workspaces/{binding['workspace_id']}/file",
            {**{key: binding[key] for key in ("req_id", "run_id", "task_id", "attempt_id")},
             "path": "README.md", "content": "stale\n", "expected_sha256": file_data["sha256"], "reason": "stale"},
            {"Idempotency-Key": "api-write-2"},
        )
        assert status == 409
        assert conflict["error"]["code"] == "FILE_VERSION_CONFLICT"
        status, diff = _request(port, "GET", f"/api/workspaces/{binding['workspace_id']}/diff?{query}&path=README.md")
        assert status == 200 and "updated" in diff["diff"]

        source_isolated = workspace_manager.provision_isolated_workspace(
            req_id="api-smoke", run_id=run_id, task_id="source", attempt_id="source-1"
        )
        target_isolated = workspace_manager.provision_isolated_workspace(
            req_id="api-smoke", run_id=run_id, task_id="target", attempt_id="target-1"
        )
        source_binding = workspace_manager.bind_attempt(
            req_id="api-smoke", run_id=run_id, task_id="source", attempt_id="source-1",
            write_scope=("README.md",), isolated_workspace_id=source_isolated["workspace_id"],
        )
        target_binding = workspace_manager.bind_attempt(
            req_id="api-smoke", run_id=run_id, task_id="target", attempt_id="target-1",
            write_scope=("README.md",), isolated_workspace_id=target_isolated["workspace_id"],
        )
        source_path = Path(workspace_manager.resolve_attempt_path(
            "api-smoke", run_id, "source", "source-1"
        ))
        (source_path / "README.md").write_text("merged from source\n", encoding="utf-8")
        merge_body = {
            "source_task_id": "source", "source_attempt_id": "source-1",
            "target_task_id": "target", "target_attempt_id": "target-1",
            "message": "merge API smoke",
        }
        status, merge = _request(
            port, "POST", f"/api/workflows/api-smoke/runs/{run_id}/merge-tasks", merge_body
        )
        assert status == 201
        merge_id = merge["merge_task"]["merge_id"]
        status, preview = _request(port, "GET", f"/api/workflows/api-smoke/runs/{run_id}/merge-tasks/{merge_id}")
        assert status == 200 and "README.md" in preview["merge_task"]["diff"]
        status, applied = _request(port, "POST", f"/api/workflows/api-smoke/runs/{run_id}/merge-tasks/{merge_id}/apply", {})
        assert status == 200 and applied["merge_task"]["status"] == "DONE"
        target_path = Path(workspace_manager.resolve_attempt_path(
            "api-smoke", run_id, "target", "target-1"
        ))
        assert (target_path / "README.md").read_text(encoding="utf-8") == "merged from source\n"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.api
def test_dashboard_create_workflow_persists_task_list():
    store = MockConsulStore()
    server = serve(
        store, host="127.0.0.1", port=0,
        auth_config=AuthConfig(mode="local", local_user="api"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        status, created = _request(
            port, "POST", "/api/workflows",
            {
                "req_id": "created-from-dashboard",
                "title": "Dashboard 创建的工作流",
                "requirement": "创建并展示任务列表",
                "published": False,
                "tasks": [
                    {"id": "design", "name": "设计", "type": "design",
                     "agent": "claude", "dependsOn": [],
                     "description": "完成设计", "acceptance": "设计通过"},
                    {"id": "test", "name": "测试", "type": "test",
                     "agent": "codex", "dependsOn": ["design"],
                     "description": "完成测试", "acceptance": "测试通过"},
                ],
            },
        )
        assert status == 201
        assert created["workflow"]["task_count"] == 2

        status, listing = _request(port, "GET", "/api/workflows")
        assert status == 200
        assert any(item["req_id"] == "created-from-dashboard"
                   and item["total_tasks"] == 2 for item in listing["workflows"])

        status, detail = _request(port, "GET", "/api/workflow/created-from-dashboard")
        assert status == 200
        assert detail["tasks"]["design"]["status"] == "PENDING"
        assert detail["tasks"]["test"]["status"] == "BLOCKED"
        assert detail["dependencies"]["test"]["depends_on"] == ["design"]
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.api
def test_dashboard_asgi_capabilities_contract():
    store = MockConsulStore()
    app = create_asgi_app(
        store, auth_config=AuthConfig(mode="local", local_user="asgi"),
        features=FeatureConfig({"sse_events": True}),
    )
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    async def call():
        await app({"type": "http", "method": "GET", "raw_path": b"/api/capabilities",
                   "query_string": b"", "headers": [], "client": ("127.0.0.1", 1)},
                  receive, send)

    asyncio.run(call())
    assert sent[0]["status"] == 200
    payload = json.loads(sent[1]["body"])
    assert payload["actor"]["subject"] == "local:asgi"
    assert "scope" in payload
