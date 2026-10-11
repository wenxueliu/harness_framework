"""HTTP contract tests for the template/version/instance product model."""
from __future__ import annotations

import http.client
import json
import threading
from urllib.parse import quote

from harness_framework.auth import AuthConfig
from harness_framework.artifacts import ArtifactCleanupWorker, ArtifactService
from harness_framework.cleanup_notifications import CleanupNotificationService
from harness_framework.event_journal import EventJournal
from harness_framework.project_groups import ProjectGroupService
from harness_framework.workspace_cleanup import WorkspaceCleanupWorker
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


def prepare_runtime_profile(port: int, group_id: str = "unassigned", profile_id: str = "profile-main"):
    status, _ = request(port, "POST", "/api/agent-runtimes", {
        "runtime_id": "runtime-main", "name": "ACP Runtime", "version": "1.0.0",
    })
    assert status == 201
    status, runtime = request(port, "POST", "/api/agent-runtimes/runtime-main/health", {
        "status": "HEALTHY",
    })
    assert status == 200 and runtime["agent_runtime"]["status"] == "HEALTHY"
    status, profile = request(port, "POST", "/api/execution-profiles", {
        "profile_id": profile_id,
        "group_id": group_id,
        "name": "Standard profile",
        "agent_runtime_id": "runtime-main",
    })
    assert status == 201
    return profile["profile_id"]


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
        assert status == 201, created
        assert created["template"]["draft_revision"] == 1
        profile_id = prepare_runtime_profile(port)

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
            "execution_profile_id": profile_id,
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
        assert started["run"]["run_id"]
        status, latest_preflight = request(
            port, "GET", f"/api/instances/{first_id}/preflight/latest",
        )
        assert status == 200 and latest_preflight["status"] == "PASSED"
        assert latest_preflight["attempt_id"] == first["instance"]["current_attempt_id"]
        status, manifest = request(
            port, "GET", f"/api/attempts/{latest_preflight['attempt_id']}/manifest",
        )
        assert status == 200 and manifest["attempt_id"] == latest_preflight["attempt_id"]
        status, artifact = request(
            port, "POST", f"/api/instances/{first_id}/artifacts",
            {"artifact_id": "release-log", "path": "logs/release.txt", "retention_days": 1},
        )
        assert status == 200 and artifact["artifact"]["instance_id"] == first_id
        status, artifacts = request(port, "GET", f"/api/instances/{first_id}/artifacts")
        assert status == 200
        assert [item["artifact_id"] for item in artifacts["artifacts"]] == ["release-log"]

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

        status, version_diff = request(
            port, "GET",
            f"/api/templates/release-template/versions/{version_v2}/diff"
            f"?from_version_id={quote(version_v1, safe='')}",
        )
        assert status == 200
        assert version_diff["removed_tasks"] == ["publish", "test"]
        assert version_diff["summary"] == "+0 -2 ~0"

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
        profile_id = prepare_runtime_profile(port)
        status, result = request(
            port, "POST", "/api/templates/execute-template/publish-and-execute",
            {"instance": {
                "instance_id": "execute-instance", "name": "Run now",
                "execution_profile_id": profile_id,
            }},
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
        profile_id = prepare_runtime_profile(port)
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
                "execution_profile_id": profile_id,
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


def test_template_editor_validation_locations_and_revision_conflict():
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
            "template_id": "editor-template",
            "name": "Editor",
            "parameter_schema": {
                "type": "object",
                "properties": {"region": {"type": "string"}},
                "required": ["region"],
            },
            "tasks": [
                {"id": "build", "type": "backend", "agent": "claude", "depends_on": []},
                {"id": "test", "type": "test", "agent": "codex", "depends_on": ["build"]},
            ],
        })
        assert status == 201

        status, conflict = request(port, "PATCH", "/api/templates/editor-template/draft", {
            "expected_revision": 0,
            "tasks": [],
        })
        assert status == 409
        assert conflict["error"]["code"] == "REVISION_CONFLICT"
        assert conflict["error"]["details"]["actual_revision"] == 1

        status, cycle = request(port, "PATCH", "/api/templates/editor-template/draft", {
            "expected_revision": 1,
            "tasks": [
                {"id": "build", "type": "backend", "agent": "claude", "depends_on": ["test"]},
                {"id": "test", "type": "test", "agent": "codex", "depends_on": ["build"]},
            ],
        })
        assert status == 422
        location = cycle["error"]["details"]["location"]
        assert location["node"] and "->" in location["edge"]
        assert isinstance(location["line"], int) and location["line"] >= 1

        status, dangling = request(port, "PATCH", "/api/templates/editor-template/draft", {
            "expected_revision": 1,
            "tasks": [{"id": "build", "type": "backend", "agent": "claude", "depends_on": ["missing"]}],
        })
        assert status == 422
        location = dangling["error"]["details"]["location"]
        assert location == {
            "node": "build", "edge": "build->missing", "field": "depends_on",
            "line": 1,
        }

        status, invalid_agent = request(port, "PATCH", "/api/templates/editor-template/draft", {
            "expected_revision": 1,
            "tasks": [{"id": "build", "type": "backend", "agent": "unknown", "depends_on": []}],
        })
        assert status == 422
        assert invalid_agent["error"]["code"] == "INVALID_AGENT"
        assert invalid_agent["error"]["details"]["location"]["field"] == "agent"

        status, empty_fields = request(port, "PATCH", "/api/templates/editor-template/draft", {
            "expected_revision": 1,
            "tasks": [{"id": "build", "type": "", "agent": "", "depends_on": []}],
        })
        assert status == 422
        assert empty_fields["error"]["code"] == "TASK_FIELD_REQUIRED"
        assert empty_fields["error"]["details"]["location"]["field"] == "type"

        status, updated = request(port, "PATCH", "/api/templates/editor-template/draft", {
            "expected_revision": 1,
            "tasks": [
                {"id": "build", "type": "backend", "agent": "claude", "depends_on": []},
                {"id": "test", "type": "test", "agent": "codex", "depends_on": ["build"]},
            ],
        })
        assert status == 200 and updated["template"]["draft_revision"] == 2

        status, _ = request(port, "POST", "/api/templates/editor-template/publish", {},
                            {"Idempotency-Key": "editor-publish"})
        assert status == 200

        status, rejected = request(port, "POST", "/api/instances", {
            "template_id": "editor-template", "name": "Missing params",
            "parameters": {}, "git": {}, "workspace": {},
        }, {"Idempotency-Key": "editor-instance-invalid"})
        assert status == 422
        assert rejected["error"]["code"] == "PARAMETER_SCHEMA_INVALID"
        assert rejected["error"]["details"]["errors"][0]["field"] == "region"

        status, created = request(port, "POST", "/api/instances", {
            "template_id": "editor-template", "name": "Schema run",
            "parameters": {"region": "cn"}, "git": {}, "workspace": {},
        }, {"Idempotency-Key": "editor-instance-valid"})
        assert status == 201 and created["instance"]["status"]["state"] == "QUEUED"
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
        profile_id = prepare_runtime_profile(port)
        instance_body = {
            "template_id": "instance-delete-template",
            "version_id": published["version"]["version_id"],
            "name": "Instance to delete",
            "parameters": {}, "git": {}, "workspace": {},
            "execution_profile_id": profile_id,
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


def test_secret_references_are_stored_separately_and_redacted_from_api_output():
    store = MockConsulStore()
    server = serve(
        store,
        host="127.0.0.1",
        port=0,
        auth_config=AuthConfig(mode="local", local_user="secret-test"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        status, _ = request(port, "POST", "/api/templates", {
            "template_id": "secret-template",
            "name": "Secret flow",
            "parameter_schema": {
                "type": "object",
                "required": ["api_key"],
                "properties": {"api_key": {"type": "string", "secret": True}},
            },
            "tasks": [{"id": "build", "type": "backend", "depends_on": []}],
        })
        assert status == 201
        status, _ = request(
            port, "POST", "/api/templates/secret-template/publish", {},
            {"Idempotency-Key": "secret-publish"},
        )
        assert status == 200
        profile_id = prepare_runtime_profile(port)

        status, created = request(port, "POST", "/api/instances", {
            "template_id": "secret-template",
            "name": "Secret instance",
            "parameters": {
                "api_key": {
                    "$ref": "secret:deploy-token",
                    "value": "super-secret-value",
                },
            },
            "execution_profile_id": profile_id,
        }, {"Idempotency-Key": "secret-instance"})
        assert status == 201, created
        instance = created["instance"]
        assert instance["context"]["parameters"]["api_key"] == "$ref:secret:deploy-token"

        status, fetched = request(
            port, "GET", f"/api/instances/{instance['instance_id']}"
        )
        assert status == 200
        assert fetched["instance"]["context"]["parameters"]["api_key"] == (
            "$ref:secret:deploy-token"
        )

        stored_value = store._store.get(
            "secrets/jobflows/instances/"
            f"{instance['instance_id']}/deploy-token"
        )
        assert stored_value == "super-secret-value"

        status, started = request(
            port, "POST", f"/api/instances/{instance['instance_id']}/start", {}
        )
        assert status == 200
        status, manifest = request(
            port,
                "GET",
                f"/api/attempts/{instance['current_attempt_id']}/manifest",
        )
        assert status == 200
        assert manifest["secret_refs"] == ["$ref:secret:deploy-token"]

        session_key = (
            f"workflows/{instance['instance_id']}/sessions/build/"
            "session-1/events/1"
        )
        store.kv_put(session_key, json.dumps({
            "seq": "1", "message": "using super-secret-value",
        }))
        status, session = request(
            port,
            "GET",
            f"/api/sessions/{instance['instance_id']}/build",
        )
        assert status == 200
        assert session["events"][0]["message"] == "using $ref:secret:deploy-token"

        event_key = f"jobflows/instances/{instance['instance_id']}/events/legacy"
        store.kv_put(event_key, json.dumps({
            "event_id": "legacy", "type": "TEST_EVENT", "actor": "system",
            "created_at": "2026-01-01T00:00:00Z",
            "data": {"log": "super-secret-value"},
        }))
        status, events = request(
            port, "GET", f"/api/instances/{instance['instance_id']}/events"
        )
        assert status == 200
        assert events["events"][0]["data"]["log"] == "$ref:secret:deploy-token"
    finally:
        server.shutdown()
        server.server_close()


def test_instance_changesets_and_events_are_queryable_and_scoped():
    store = MockConsulStore()
    groups = ProjectGroupService(store)
    group = groups.create(name="Scoped", description="scoped", actor="alice")
    groups.put_member(group["group_id"], "viewer", "VIEWER")
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
    viewer = {"X-Harness-Subject": "viewer"}
    try:
        status, _ = request(port, "POST", "/api/templates", {
            "template_id": "changeset-template",
            "name": "Changeset flow",
            "group_id": group["group_id"],
            "tasks": {
                "build": {"type": "backend", "depends_on": []},
                "test": {"type": "test", "depends_on": ["build"]},
            },
        }, alice)
        assert status == 201
        status, published = request(
            port, "POST", "/api/templates/changeset-template/publish", {},
            {**alice, "Idempotency-Key": "changeset-publish"},
        )
        assert status == 200
        status, created = request(port, "POST", "/api/instances", {
            "template_id": "changeset-template",
            "version_id": published["version"]["version_id"],
            "name": "Scoped instance",
            "parameters": {},
        }, {**alice, "Idempotency-Key": "changeset-instance"})
        assert status == 201
        instance_id = created["instance"]["instance_id"]

        status, changeset = request(
            port, "POST", f"/api/instances/{instance_id}/changesets",
            {"changed_tasks": ["build"], "changes": {"build": {"type": "frontend"}}},
            alice,
        )
        assert status == 200
        changeset_id = changeset["changeset"]["changeset_id"]
        assert changeset["changeset"]["impacted_tasks"] == ["build", "test"]

        status, readable = request(
            port, "GET", f"/api/instances/{instance_id}/changesets",
            headers=viewer,
        )
        assert status == 200
        assert [item["changeset_id"] for item in readable["changesets"]] == [changeset_id]

        status, applied = request(
            port,
            "POST",
            f"/api/instances/{instance_id}/changesets/{changeset_id}/apply",
            {"start": False, "instance_id": "successor-instance"},
            {**alice, "Idempotency-Key": "changeset-apply"},
        )
        assert status == 200
        assert applied["changeset"]["state"] == "APPLIED"
        assert applied["successor_of"] == instance_id

        status, events = request(
            port, "GET", f"/api/instances/{instance_id}/events", headers=viewer
        )
        assert status == 200
        event_types = [item["type"] for item in events["events"]]
        assert "INSTANCE_CREATED" in event_types
        assert "INSTANCE_STATUS_CHANGED" in event_types
    finally:
        server.shutdown()
        server.server_close()


def test_cleanup_workers_record_admin_notifications_before_deletion():
    store = MockConsulStore()

    class FakeWorkspaceManager:
        def __init__(self, store):
            self.store = store

        def trash_run_workspace(self, req_id, run_id):
            return {"run_workspace_id": "rws-trash"}

    worker = WorkspaceCleanupWorker(
        FakeWorkspaceManager(store), interval=1, grace_hours=1,
        event_journal=EventJournal(store),
    )
    store.kv_put("workflows/cleanup-req/project-group", "grp_admin")
    store.kv_put(
        "workflows/cleanup-req/runs/run-1/workspace/record",
        json.dumps({"status": "CLEANUP_PENDING", "run_workspace_id": "rws-trash"}),
    )
    result = worker.run_once()
    assert result["trashed"] == ["rws-trash"]
    assert len(result["notified"]) == 1
    notifications = CleanupNotificationService(store).list_for_group("grp_admin")
    assert notifications[0]["action"] == "TRASH"
    journal = EventJournal(store).replay(limit=10)[0]
    assert journal[0].type == "CLEANUP_ADMIN_NOTIFIED"

    artifacts = ArtifactService(store, CleanupNotificationService(store))
    store.kv_put("artifacts/artifact-instance/art-1", json.dumps({
        "artifact_id": "art-1",
        "instance_id": "artifact-instance",
        "path": "reports/out.txt",
        "status": "ACTIVE",
        "retention_until": "2020-01-01T00:00:00Z",
    }))
    cleanup = ArtifactCleanupWorker(artifacts, interval=1).run_once()
    assert cleanup["expired"] == ["art-1"]
    notifications = CleanupNotificationService(store).list_for_group("unassigned")
    assert notifications[-1]["resource_type"] == "ARTIFACT"
    assert notifications[-1]["action"] == "EXPIRE"


def _start_running_instance(store, port: int, template_id: str, name: str):
    """Publish a one-task template, create and start an instance; return ids."""
    status, _ = request(port, "POST", "/api/templates", {
        "template_id": template_id,
        "name": name,
        "tasks": [{"id": "build", "type": "backend", "depends_on": []}],
    })
    assert status == 201
    profile_id = prepare_runtime_profile(port)
    status, published = request(
        port, "POST", f"/api/templates/{template_id}/publish", {},
        {"Idempotency-Key": f"publish-{template_id}"},
    )
    assert status == 200
    status, created = request(port, "POST", "/api/instances", {
        "template_id": template_id,
        "version_id": published["version"]["version_id"],
        "name": name,
        "parameters": {}, "git": {}, "workspace": {},
        "execution_profile_id": profile_id,
    }, {"Idempotency-Key": f"instance-{template_id}"})
    assert status == 201, created
    instance_id = created["instance"]["instance_id"]
    status, started = request(port, "POST", f"/api/instances/{instance_id}/start", {})
    assert status == 200
    assert started["instance"]["status"]["state"] == "RUNNING"
    return store._store[f"jobflows/instances/{instance_id}/current_run"], instance_id


def test_instance_task_attempts_sessions_and_logs_are_queryable():
    store = MockConsulStore()
    server = serve(
        store,
        host="127.0.0.1",
        port=0,
        auth_config=AuthConfig(mode="local", local_user="attempt-test"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        run_id, instance_id = _start_running_instance(store, port, "attempt-template", "Attempts")

        status, retried = request(
            port, "POST", f"/api/instances/{instance_id}/tasks/build/retry", {},
        )
        assert status == 200, retried
        new_attempt_id = retried["attempt_id"]

        store.kv_put(
            f"workflows/{instance_id}/runs/{run_id}/tasks/build/attempts/"
            f"{new_attempt_id}/workspace-binding",
            json.dumps({
                "binding_id": "awb_1",
                "workspace_id": "rws-attempt",
                "binding_type": "RUN_SHARED",
                "writable": True,
                "bound_at": "2026-01-01T00:00:00Z",
            }),
        )
        for seq, (attempt_id, message) in enumerate([
            (new_attempt_id, "started build"),
            ("attempt-other", "other attempt event"),
            (new_attempt_id, "rebuild done"),
        ], start=1):
            store.kv_put(
                f"workflows/{instance_id}/sessions/build/session-1/events/{seq}",
                json.dumps({
                    "seq": str(seq), "message": message, "attempt_id": attempt_id,
                    "timestamp": f"2026-01-01T00:0{seq - 1}:00Z",
                }),
            )

        status, attempts = request(
            port, "GET", f"/api/instances/{instance_id}/tasks/build/attempts",
        )
        assert status == 200
        assert [item["attempt_id"] for item in attempts["attempts"]] == [new_attempt_id]
        attempt = attempts["attempts"][0]
        assert attempt["state"] == "PENDING"
        assert attempt["workspace_binding"]["workspace_id"] == "rws-attempt"
        assert attempt["workspace_binding"]["binding_type"] == "RUN_SHARED"

        status, logs = request(
            port, "GET",
            f"/api/instances/{instance_id}/tasks/build/attempts/{new_attempt_id}/logs",
        )
        assert status == 200
        assert logs["attempt_id"] == new_attempt_id
        assert [event["message"] for event in logs["events"]] == [
            "started build", "rebuild done",
        ]
        assert logs["total"] == 2

        status, sessions = request(
            port, "GET", f"/api/instances/{instance_id}/sessions/build",
        )
        assert status == 200
        assert sessions["instance_id"] == instance_id
        assert sessions["total"] == 3

        status, missing_task = request(
            port, "GET", f"/api/instances/{instance_id}/tasks/missing/attempts",
        )
        assert status == 404
        assert missing_task["error"]["code"] == "TASK_NOT_FOUND"

        status, missing_attempt = request(
            port, "GET",
            f"/api/instances/{instance_id}/tasks/build/attempts/attempt-none/logs",
        )
        assert status == 404
        assert missing_attempt["error"]["code"] == "TASK_ATTEMPT_NOT_FOUND"
    finally:
        server.shutdown()
        server.server_close()


def test_instance_preflight_can_be_retriggered_after_fix():
    store = MockConsulStore()
    server = serve(
        store,
        host="127.0.0.1",
        port=0,
        auth_config=AuthConfig(mode="local", local_user="preflight-test"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        status, _ = request(port, "POST", "/api/templates", {
            "template_id": "preflight-template",
            "name": "Preflight",
            "tasks": [{"id": "build", "type": "backend", "depends_on": []}],
        })
        assert status == 201
        status, published = request(
            port, "POST", "/api/templates/preflight-template/publish", {},
            {"Idempotency-Key": "preflight-publish"},
        )
        assert status == 200
        status, created = request(port, "POST", "/api/instances", {
            "template_id": "preflight-template",
            "version_id": published["version"]["version_id"],
            "name": "No profile yet",
            "parameters": {}, "git": {}, "workspace": {},
        }, {"Idempotency-Key": "preflight-instance"})
        assert status == 201
        instance_id = created["instance"]["instance_id"]

        status, blocked = request(
            port, "POST", f"/api/instances/{instance_id}/preflight", {},
        )
        assert status == 200
        assert blocked["preflight"]["status"] == "BLOCKED"
        runtime_check = next(
            item for item in blocked["preflight"]["checks"]
            if item["check_id"] == "agent-runtime"
        )
        assert runtime_check["status"] == "FAIL"

        profile_id = prepare_runtime_profile(port)
        status, updated = request(port, "PATCH", f"/api/instances/{instance_id}", {
            "execution_profile_id": profile_id,
        })
        assert status == 200

        status, passed = request(
            port, "POST", f"/api/instances/{instance_id}/preflight", {},
        )
        assert status == 200
        assert passed["preflight"]["status"] == "PASSED"
        assert passed["preflight"]["manifest_id"]

        status, latest = request(
            port, "GET", f"/api/instances/{instance_id}/preflight/latest",
        )
        assert status == 200
        assert latest["status"] == "PASSED"
        assert latest["manifest_id"] == passed["preflight"]["manifest_id"]

        status, missing = request(
            port, "POST", "/api/instances/instance-missing/preflight", {},
        )
        assert status == 404
        assert missing["error"]["code"] == "INSTANCE_NOT_FOUND"
    finally:
        server.shutdown()
        server.server_close()


def test_execution_manifest_alias_matches_canonical_manifest():
    store = MockConsulStore()
    server = serve(
        store,
        host="127.0.0.1",
        port=0,
        auth_config=AuthConfig(mode="local", local_user="manifest-test"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        run_id, instance_id = _start_running_instance(store, port, "alias-template", "Alias")
        attempt_id = f"attempt-{instance_id}"

        status, alias = request(
            port, "GET", f"/api/attempts/{attempt_id}/execution-manifest",
        )
        assert status == 200
        assert alias["attempt_id"] == attempt_id
        assert alias["execution_profile"]["profile_id"] == "profile-main"

        status, canonical = request(
            port, "GET", f"/api/attempts/{attempt_id}/manifest",
        )
        assert status == 200
        assert canonical["manifest_id"] == alias["manifest_id"]

        status, missing = request(
            port, "GET", f"/api/attempts/attempt-none/execution-manifest",
        )
        assert status == 404
        assert missing["error"]["code"] == "MANIFEST_NOT_FOUND"
    finally:
        server.shutdown()
        server.server_close()


def test_audit_query_filters_journal_events():
    store = MockConsulStore()
    journal = EventJournal(store)
    journal.append("TASK_STATUS_CHANGED", subject={
        "group_id": "grp-a", "instance_id": "inst-1",
    }, actor={"type": "human", "id": "alice"}, data={"state": "RUNNING"})
    journal.append("RUN_STATUS_CHANGED", subject={
        "group_id": "grp-a", "req_id": "inst-1",
    }, actor={"type": "system", "id": "dispatcher"}, data={"state": "RUNNING"})
    journal.append("WORKFLOW_CREATED", subject={
        "group_id": "grp-b", "req_id": "inst-2",
    }, actor={"type": "human", "id": "alice"}, data={"title": "other group"})
    server = serve(
        store,
        host="127.0.0.1",
        port=0,
        auth_config=AuthConfig(mode="local", local_user="audit-test"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        status, all_events = request(port, "GET", "/api/audit")
        assert status == 200
        assert all_events["total"] == 3

        status, scoped = request(port, "GET", "/api/audit?group_id=grp-a")
        assert status == 200
        assert scoped["total"] == 2

        status, by_instance = request(port, "GET", "/api/audit?instance_id=inst-1")
        assert status == 200
        assert by_instance["total"] == 2
        assert {event["type"] for event in by_instance["events"]} == {
            "TASK_STATUS_CHANGED", "RUN_STATUS_CHANGED",
        }

        status, by_actor = request(port, "GET", "/api/audit?actor=alice")
        assert status == 200
        assert by_actor["total"] == 2

        status, by_type = request(
            port, "GET", "/api/audit?group_id=grp-a&type=RUN_STATUS_CHANGED",
        )
        assert status == 200
        assert by_type["total"] == 1
        assert by_type["events"][0]["actor"]["id"] == "dispatcher"

        status, limited = request(port, "GET", "/api/audit?limit=1")
        assert status == 200
        assert limited["total"] == 3
        assert len(limited["events"]) == 1

        status, invalid = request(port, "GET", "/api/audit?limit=abc")
        assert status == 400
        assert invalid["error"]["code"] == "INVALID_LIMIT"
    finally:
        server.shutdown()
        server.server_close()


def test_audit_query_requires_group_scope_for_proxy_users():
    store = MockConsulStore()
    groups = ProjectGroupService(store)
    group = groups.create(name="Audited", description="audit", actor="alice")
    groups.put_member(group["group_id"], "viewer", "VIEWER")
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
    viewer = {"X-Harness-Subject": "viewer"}
    outsider = {"X-Harness-Subject": "mallory"}
    try:
        status, unscooped = request(port, "GET", "/api/audit", headers=alice)
        assert status == 422
        assert unscooped["error"]["code"] == "AUDIT_SCOPE_REQUIRED"

        status, forbidden = request(
            port, "GET", f"/api/audit?group_id={group['group_id']}", headers=outsider,
        )
        assert status == 403
        assert forbidden["error"]["code"] == "FORBIDDEN"

        status, events = request(
            port, "GET", f"/api/audit?group_id={group['group_id']}", headers=viewer,
        )
        assert status == 200
        assert events["events"] == []
    finally:
        server.shutdown()
        server.server_close()
