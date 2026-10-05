"""HTTP contract tests for the template/version/instance product model."""
from __future__ import annotations

import http.client
import json
import threading

from harness_framework.auth import AuthConfig
from harness_framework.project_groups import ProjectGroupService
from harness_framework.webapi import serve
from tests.conftest import MockConsulStore


def request(port: int, method: str, path: str, body: dict | None = None, headers: dict | None = None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(
        method, path,
        body=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    response = connection.getresponse()
    payload = json.loads(response.read() or b"{}")
    connection.close()
    return response.status, payload


def test_template_version_and_multiple_instance_lifecycle():
    server = serve(
        MockConsulStore(),
        host="127.0.0.1",
        port=0,
        auth_config=AuthConfig(mode="local", local_user="job-flow-test"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        status, created = request(port, "POST", "/api/templates", {
            "template_id": "release-template",
            "name": "Release",
            "tasks": {
                "build": {"type": "backend", "depends_on": []},
                "test": {"type": "test", "depends_on": ["build"]},
            },
        })
        assert status == 201
        assert created["template"]["draft_revision"] == 1

        status, updated = request(port, "PATCH", "/api/templates/release-template/draft", {
            "expected_revision": 1,
            "description": "manual release flow",
            "tasks": [
                {"id": "build", "type": "backend", "depends_on": []},
                {"id": "test", "type": "test", "depends_on": ["build"]},
                {"id": "publish", "type": "deploy", "depends_on": ["test"]},
            ],
        })
        assert status == 200
        assert updated["template"]["draft_revision"] == 2

        status, validation = request(port, "POST", "/api/templates/release-template/validate", {})
        assert status == 200 and validation["validation"]["valid"] is True

        status, published_v1 = request(
            port, "POST", "/api/templates/release-template/publish", {},
            {"Idempotency-Key": "publish-release-v1"},
        )
        assert status == 200
        version_v1 = published_v1["version"]["version_id"]

        instance_body = {
            "template_id": "release-template",
            "version_id": version_v1,
            "name": "release-main",
            "parameters": {"region": "cn"},
            "git": {"ref": "main"},
            "workspace": {"workspace_id": "ws-main"},
        }
        status, first = request(
            port, "POST", "/api/instances", instance_body,
            {"Idempotency-Key": "instance-main"},
        )
        assert status == 201
        first_id = first["instance"]["instance_id"]

        status, duplicate = request(
            port, "POST", "/api/instances", instance_body,
            {"Idempotency-Key": "instance-main"},
        )
        assert status == 201
        assert duplicate["instance"]["instance_id"] == first_id

        second_body = {**instance_body, "name": "release-hotfix", "git": {"ref": "hotfix"}}
        status, second = request(
            port, "POST", "/api/instances", second_body,
            {"Idempotency-Key": "instance-hotfix"},
        )
        assert status == 201
        assert second["instance"]["instance_id"] != first_id

        status, listing = request(port, "GET", "/api/instances?template_id=release-template")
        assert status == 200
        assert len(listing["instances"]) == 2
        listed_first = next(item for item in listing["instances"] if item["instance_id"] == first_id)
        assert listed_first["status"]["state"] == "QUEUED"

        status, started = request(port, "POST", f"/api/instances/{first_id}/start", {})
        assert status == 200 and started["instance"]["status"]["state"] == "RUNNING"

        status, rerun = request(
            port, "POST", f"/api/instances/{first_id}/rerun",
            {"parameters": {"region": "us"}, "git": {"ref": "experiment"}},
            {"Idempotency-Key": "rerun-main"},
        )
        assert status == 200
        successor = rerun["instance"]
        assert successor["successor_of"] == first_id
        assert successor["version_id"] == version_v1
        assert successor["context"]["git"]["ref"] == "experiment"
        status, old = request(port, "GET", f"/api/instances/{first_id}")
        assert status == 200 and old["instance"]["status"]["state"] == "DRAINING"

        status, draft_v2 = request(port, "PATCH", "/api/templates/release-template/draft", {
            "expected_revision": 2,
            "tasks": [{"id": "build", "type": "backend", "depends_on": []}],
        })
        assert status == 200
        status, published_v2 = request(
            port, "POST", "/api/templates/release-template/publish", {},
            {"Idempotency-Key": "publish-release-v2"},
        )
        assert status == 200
        version_v2 = published_v2["version"]["version_id"]
        assert version_v2 != version_v1

        status, old_version = request(
            port, "GET", f"/api/templates/release-template/versions/{version_v1}"
        )
        assert status == 200
        assert len(old_version["manifest"]["tasks"]) == 3

        status, disabled = request(
            port, "POST",
            f"/api/templates/release-template/versions/{version_v2}/disable", {},
        )
        assert status == 200
        status, rejected = request(
            port, "POST", "/api/instances",
            {**instance_body, "version_id": version_v2},
            {"Idempotency-Key": "instance-disabled"},
        )
        assert status == 422
        assert rejected["error"]["code"] == "VERSION_NOT_AVAILABLE"
    finally:
        server.shutdown()
        server.server_close()


def test_publish_and_execute_is_one_idempotent_action():
    server = serve(
        MockConsulStore(),
        host="127.0.0.1",
        port=0,
        auth_config=AuthConfig(mode="local", local_user="job-flow-test"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        status, _ = request(port, "POST", "/api/templates", {
            "template_id": "execute-template",
            "name": "Execute",
            "tasks": [{"id": "build", "depends_on": []}],
        })
        assert status == 201
        status, result = request(
            port, "POST", "/api/templates/execute-template/publish-and-execute",
            {"instance": {"instance_id": "execute-instance", "name": "Run now"}},
            {"Idempotency-Key": "publish-and-execute"},
        )
        assert status == 200
        assert result["instance"]["instance_id"] == "execute-instance"
        assert result["instance"]["status"]["state"] == "RUNNING"
        assert result["run"]["status"] == "RUNNING"
        status, duplicate = request(
            port, "POST", "/api/templates/execute-template/publish-and-execute",
            {"instance": {"instance_id": "execute-instance", "name": "Run now"}},
            {"Idempotency-Key": "publish-and-execute"},
        )
        assert status == 200
        assert duplicate["instance"]["instance_id"] == "execute-instance"
        assert duplicate["run"]["run_id"] == result["run"]["run_id"]
    finally:
        server.shutdown()
        server.server_close()


def test_template_delete_requires_no_bound_instances():
    server = serve(
        MockConsulStore(),
        host="127.0.0.1",
        port=0,
        auth_config=AuthConfig(mode="local", local_user="job-flow-test"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        status, _ = request(port, "POST", "/api/templates", {
            "template_id": "deletable-template",
            "name": "Deletable",
            "tasks": [{"id": "build", "depends_on": []}],
        })
        assert status == 201

        status, deleted = request(port, "DELETE", "/api/templates/deletable-template")
        assert status == 200 and deleted["deleted"] is True
        status, missing = request(port, "GET", "/api/templates/deletable-template")
        assert status == 404
        assert missing["error"]["code"] == "TEMPLATE_NOT_FOUND"

        status, _ = request(port, "POST", "/api/templates", {
            "template_id": "bound-template",
            "name": "Bound",
            "tasks": [{"id": "build", "depends_on": []}],
        })
        assert status == 201
        status, published = request(
            port, "POST", "/api/templates/bound-template/publish", {},
            {"Idempotency-Key": "publish-bound-template"},
        )
        assert status == 200
        status, created = request(
            port, "POST", "/api/instances", {
                "template_id": "bound-template",
                "version_id": published["version"]["version_id"],
                "name": "Bound instance",
                "parameters": {}, "git": {}, "workspace": {},
            },
            {"Idempotency-Key": "bound-instance"},
        )
        assert status == 201

        status, rejected = request(port, "DELETE", "/api/templates/bound-template")
        assert status == 409
        assert rejected["error"]["code"] == "TEMPLATE_HAS_INSTANCES"
        assert created["instance"]["instance_id"] in rejected["error"]["details"]["instance_ids"]
    finally:
        server.shutdown()
        server.server_close()


def test_instance_delete_requires_terminal_state_and_removes_instance():
    server = serve(
        MockConsulStore(),
        host="127.0.0.1",
        port=0,
        auth_config=AuthConfig(mode="local", local_user="job-flow-test"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        status, _ = request(port, "POST", "/api/templates", {
            "template_id": "instance-delete-template",
            "name": "Instance delete",
            "tasks": [{"id": "build", "depends_on": []}],
        })
        assert status == 201
        status, published = request(
            port, "POST", "/api/templates/instance-delete-template/publish", {},
            {"Idempotency-Key": "publish-instance-delete-template"},
        )
        assert status == 200
        instance_body = {
            "template_id": "instance-delete-template",
            "version_id": published["version"]["version_id"],
            "name": "Instance to delete",
            "parameters": {}, "git": {}, "workspace": {},
        }
        status, created = request(
            port, "POST", "/api/instances", instance_body,
            {"Idempotency-Key": "instance-to-delete"},
        )
        assert status == 201
        instance_id = created["instance"]["instance_id"]

        status, deleted = request(port, "DELETE", f"/api/instances/{instance_id}")
        assert status == 200 and deleted["deleted"] is True
        status, missing = request(port, "GET", f"/api/instances/{instance_id}")
        assert status == 404
        assert missing["error"]["code"] == "INSTANCE_NOT_FOUND"

        status, created = request(
            port, "POST", "/api/instances", {**instance_body, "name": "Running instance"},
            {"Idempotency-Key": "running-instance"},
        )
        assert status == 201
        running_id = created["instance"]["instance_id"]
        status, started = request(port, "POST", f"/api/instances/{running_id}/start", {})
        assert status == 200
        assert started["instance"]["status"]["state"] == "RUNNING"
        status, rejected = request(port, "DELETE", f"/api/instances/{running_id}")
        assert status == 409
        assert rejected["error"]["code"] == "INSTANCE_NOT_DELETABLE"
        assert rejected["error"]["details"]["state"] == "RUNNING"
    finally:
        server.shutdown()
        server.server_close()


def test_templates_and_instances_are_scoped_to_project_group():
    store = MockConsulStore()
    groups = ProjectGroupService(store)
    group_a = groups.create(name="Project A", description="A", actor="alice")
    group_b = groups.create(name="Project B", description="B", actor="bob")
    server = serve(
        store,
        host="127.0.0.1",
        port=0,
        auth_config=AuthConfig(mode="trusted-proxy"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    alice = {"X-Harness-Subject": "alice"}
    bob = {"X-Harness-Subject": "bob"}
    try:
        status, template_a = request(
            port, "POST", "/api/templates",
            {
                "template_id": "group-a-template", "name": "A flow",
                "group_id": group_a["group_id"],
                "tasks": [{"id": "build", "depends_on": []}],
            },
            alice,
        )
        assert status == 201
        status, _ = request(
            port, "POST", "/api/templates",
            {
                "template_id": "group-b-template", "name": "B flow",
                "group_id": group_b["group_id"],
                "tasks": [{"id": "build", "depends_on": []}],
            },
            bob,
        )
        assert status == 201

        status, missing_scope = request(port, "GET", "/api/templates", headers=alice)
        assert status == 422
        assert missing_scope["error"]["code"] == "PROJECT_GROUP_REQUIRED"
        status, scoped = request(
            port, "GET", f"/api/templates?group_id={group_a['group_id']}", headers=alice,
        )
        assert status == 200
        assert [item["template_id"] for item in scoped["templates"]] == ["group-a-template"]
        status, forbidden = request(
            port, "GET", f"/api/templates?group_id={group_b['group_id']}", headers=alice,
        )
        assert status == 403
        assert forbidden["error"]["code"] == "FORBIDDEN"

        status, published = request(
            port, "POST", "/api/templates/group-a-template/publish", {},
            {**alice, "Idempotency-Key": "publish-group-a"},
        )
        assert status == 200
        instance_body = {
            "template_id": "group-a-template",
            "version_id": published["version"]["version_id"],
            "name": "A instance", "parameters": {}, "git": {}, "workspace": {},
        }
        status, mismatch = request(
            port, "POST", "/api/instances",
            {**instance_body, "group_id": group_b["group_id"]},
            {**alice, "Idempotency-Key": "wrong-instance-group"},
        )
        assert status == 422
        assert mismatch["error"]["code"] == "GROUP_SCOPE_MISMATCH"

        status, created = request(
            port, "POST", "/api/instances", instance_body,
            {**alice, "Idempotency-Key": "group-a-instance"},
        )
        assert status == 201
        assert created["instance"]["template_id"] == "group-a-template"
        status, instances = request(
            port, "GET", f"/api/instances?group_id={group_a['group_id']}", headers=alice,
        )
        assert status == 200
        assert [item["instance_id"] for item in instances["instances"]] == [
            created["instance"]["instance_id"]
        ]
        status, instance_scope = request(port, "GET", "/api/instances", headers=alice)
        assert status == 422
        assert instance_scope["error"]["code"] == "PROJECT_GROUP_REQUIRED"

        status, archived = request(
            port, "PATCH", f"/api/project-groups/{group_a['group_id']}",
            {"expected_revision": group_a["revision"], "status": "ARCHIVED"},
            alice,
        )
        assert status == 200
        status, rejected_after_archive = request(
            port, "POST", "/api/instances",
            {
                **instance_body, "instance_id": "archived-group-instance",
            },
            {**alice, "Idempotency-Key": "archived-group-instance"},
        )
        assert status == 409
        assert rejected_after_archive["error"]["code"] == "GROUP_ARCHIVED"
    finally:
        server.shutdown()
        server.server_close()


def test_template_archive_blocks_new_instances():
    server = serve(
        MockConsulStore(),
        host="127.0.0.1",
        port=0,
        auth_config=AuthConfig(mode="local", local_user="archive-test"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        status, created = request(port, "POST", "/api/templates", {
            "template_id": "archive-template",
            "name": "Archive test",
            "tasks": [{"id": "build", "type": "backend", "depends_on": []}],
        })
        assert status == 201

        status, published = request(
            port, "POST", "/api/templates/archive-template/publish", {},
            {"Idempotency-Key": "archive-publish"},
        )
        assert status == 200
        version_id = published["version"]["version_id"]

        status, instance = request(port, "POST", "/api/instances", {
            "template_id": "archive-template",
            "version_id": version_id,
            "name": "pre-archive-instance",
            "parameters": {}, "git": {}, "workspace": {},
        }, {"Idempotency-Key": "pre-archive-instance"})
        assert status == 201

        status, delete_rejected = request(port, "DELETE", "/api/templates/archive-template")
        assert status == 409
        assert delete_rejected["error"]["code"] == "TEMPLATE_HAS_INSTANCES"

        status, archived = request(
            port, "POST", "/api/templates/archive-template/archive", {},
        )
        assert status == 200
        assert archived["template"]["status"] == "ARCHIVED"

        status, double_archive = request(
            port, "POST", "/api/templates/archive-template/archive", {},
        )
        assert status == 409
        assert double_archive["error"]["code"] == "TEMPLATE_ALREADY_ARCHIVED"

        status, blocked = request(port, "POST", "/api/instances", {
            "template_id": "archive-template",
            "version_id": version_id,
            "instance_id": "post-archive-instance",
            "name": "post-archive-instance",
            "parameters": {}, "git": {}, "workspace": {},
        }, {"Idempotency-Key": "post-archive-inst"})
        assert status == 409
        assert blocked["error"]["code"] == "TEMPLATE_ARCHIVED"

        status, fetched = request(port, "GET", "/api/templates/archive-template")
        assert status == 200
        assert fetched["template"]["status"] == "ARCHIVED"
    finally:
        server.shutdown()
        server.server_close()
