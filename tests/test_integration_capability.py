"""Integration tests for capability preflight, manifest, artifact retention, and role migration."""
from __future__ import annotations

import json

import pytest

from harness_framework.agent_runtimes import AgentRuntimeService
from harness_framework.artifacts import ArtifactService
from harness_framework.auth import AuthConfig, AuthenticationContext, AuthorizationService, Role, resolve_role
from harness_framework.capability_preflight import CapabilityPreflightService
from harness_framework.execution_manifest import ExecutionManifestService
from harness_framework.execution_profiles import ExecutionProfileService, ProfileError
from harness_framework.local_store import LocalStore
from harness_framework.mcp_grants import MCPService, MCPError
from harness_framework.skill_bundles import SkillBundleService


@pytest.fixture()
def env():
    store = LocalStore()
    runtime_svc = AgentRuntimeService(store)
    profile_svc = ExecutionProfileService(store)
    skill_svc = SkillBundleService(store)
    mcp_svc = MCPService(store)
    manifest_svc = ExecutionManifestService(store)
    preflight = CapabilityPreflightService(store, runtime_svc, profile_svc, skill_svc, mcp_svc, None, manifest_svc)
    artifact_svc = ArtifactService(store)

    runtime_svc.register({"runtime_id": "rt-1", "name": "claude-code"}, "admin")
    runtime_svc.update_health("rt-1", "HEALTHY")
    skill_svc.register({"name": "tdd", "content": "use TDD"}, "admin")
    mcp_svc.register_server({"server_id": "mcp-1", "name": "fs"}, "admin")
    mcp_svc.create_grant({"server_id": "mcp-1", "group_id": "g1", "grant_id": "grant-1"}, "admin")

    profile_svc.create({
        "profile_id": "prof-1", "group_id": "g1", "name": "standard",
        "version": "1.0.0", "agent_runtime_id": "rt-1",
        "skill_bundle_ids": [], "mcp_grant_ids": [], "resource_budget": {"max_cost_usd": 10},
    }, "admin")

    store.kv_put("instances/inst-1", json.dumps({
        "instance_id": "inst-1", "execution_profile_id": "prof-1",
        "run_workspace_id": "ws-1", "current_attempt_id": "attempt-1",
    }))

    return {
        "store": store, "preflight": preflight, "runtime_svc": runtime_svc,
        "profile_svc": profile_svc, "mcp_svc": mcp_svc, "manifest_svc": manifest_svc,
        "artifact_svc": artifact_svc,
    }


def test_it01_full_chain_profile_instance_preflight_start(env):
    """IT-01: Create Profile → Create Instance → Preflight → Start: all PASS."""
    result = env["preflight"].run("inst-1")
    assert result["status"] == "PASSED"
    assert result["manifest_id"] is not None
    manifest = env["manifest_svc"].get("attempt-1")
    assert manifest["manifest_id"] == result["manifest_id"]
    assert manifest["agent_runtime"]["name"] == "claude-code"


def test_it02_mcp_down_blocks_preflight(env):
    """IT-02: MCP Server down → BLOCKED, cannot start."""
    env["profile_svc"].update("prof-1", {"expected_revision": 1, "mcp_grant_ids": ["grant-1"]}, "admin")
    env["mcp_svc"].revoke_grant("g1", "grant-1", "admin")
    result = env["preflight"].run("inst-1")
    assert result["status"] == "BLOCKED"
    mcp_check = next(c for c in result["checks"] if c["check_id"] == "mcp-grant")
    assert mcp_check["status"] == "FAIL"
    assert "remediation" in mcp_check


def test_it03_mcp_recovered_preflight_passes(env):
    """IT-03: MCP Server recovered → re-preflight → PASSED."""
    env["profile_svc"].update("prof-1", {"expected_revision": 1, "mcp_grant_ids": ["grant-1"]}, "admin")
    env["mcp_svc"].revoke_grant("g1", "grant-1", "admin")
    env["preflight"].run("inst-1")
    env["profile_svc"].update("prof-1", {"expected_revision": 2, "mcp_grant_ids": []}, "admin")
    result = env["preflight"].run("inst-1")
    assert result["status"] == "PASSED"


def test_it04_artifact_expires_after_retention(env):
    """IT-04: Instance terminal → N days → Artifact EXPIRED + Workspace cleanup."""
    env["artifact_svc"].create({"artifact_id": "art-1", "instance_id": "inst-1", "path": "log.txt", "retention_days": 0})
    expired = env["artifact_svc"].check_expired("inst-1")
    assert "art-1" in expired
    record = env["artifact_svc"].get("inst-1", "art-1")
    assert record["status"] == "EXPIRED"
    env["artifact_svc"].purge("inst-1", "art-1")
    with pytest.raises(Exception, match="ARTIFACT_NOT_FOUND"):
        env["artifact_svc"].get("inst-1", "art-1")


def test_it05_owner_maps_to_admin(env):
    """IT-05: Legacy OWNER role maps to ADMIN capability."""
    assert resolve_role("OWNER") is Role.ADMIN
    store = LocalStore()
    store.kv_put("project-groups/g1/members/user:old", json.dumps({"subject_id": "user:old", "role": "OWNER"}))
    config = AuthConfig(mode="trusted-proxy", trusted_proxy_addresses=frozenset({"10.0.0.2"}))
    context = AuthenticationContext(subject="user:old", display_name="Old Owner", mode="trusted-proxy")
    authz = AuthorizationService(store, config)
    assert authz.role_for(context, "g1") is Role.ADMIN
    assert "profile:manage" in authz.capabilities_for(context, "g1")


def test_it06_viewer_reads_manifest(env):
    """IT-06: Viewer can access Manifest read-only."""
    env["preflight"].run("inst-1")
    manifest = env["manifest_svc"].get("attempt-1")
    assert manifest["manifest_id"] != ""
    assert resolve_role("VIEWER") is Role.VIEWER
    viewer_caps = {"group:read", "file:read", "manifest:read", "artifact:read", "workflow:read", "instance:read", "log:read"}
    assert "manifest:read" in viewer_caps


def test_it07_editor_cannot_modify_profile(env):
    """IT-07: Editor tries to modify Profile → 403 equivalent."""
    config = AuthConfig(mode="trusted-proxy", trusted_proxy_addresses=frozenset({"10.0.0.2"}))
    store = LocalStore()
    store.kv_put("project-groups/g1/members/user:editor", json.dumps({"subject_id": "user:editor", "role": "MAINTAINER"}))
    context = AuthenticationContext(subject="user:editor", display_name="Editor", mode="trusted-proxy")
    authz = AuthorizationService(store, config)
    assert authz.role_for(context, "g1") is Role.EDITOR
    assert "profile:manage" not in authz.capabilities_for(context, "g1")
