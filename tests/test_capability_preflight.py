import pytest
from harness_framework.capability_preflight import CapabilityPreflightService
from harness_framework.execution_manifest import ExecutionManifestService
from harness_framework.agent_runtimes import AgentRuntimeService
from harness_framework.execution_profiles import ExecutionProfileService
from harness_framework.skill_bundles import SkillBundleService
from harness_framework.mcp_grants import MCPService
from harness_framework.local_store import LocalStore

@pytest.fixture()
def env():
    store = LocalStore()
    runtime_svc = AgentRuntimeService(store)
    profile_svc = ExecutionProfileService(store)
    skill_svc = SkillBundleService(store)
    mcp_svc = MCPService(store)
    manifest_svc = ExecutionManifestService(store)
    preflight = CapabilityPreflightService(store, runtime_svc, profile_svc, skill_svc, mcp_svc, None, manifest_svc)

    runtime_svc.register({"runtime_id": "rt-1", "name": "claude-code"}, "admin")
    runtime_svc.update_health("rt-1", "HEALTHY")
    skill_svc.register({"name": "tdd", "content": "use TDD"}, "admin")
    mcp_svc.register_server({"server_id": "mcp-1", "name": "fs"}, "admin")
    mcp_svc.create_grant({"server_id": "mcp-1", "group_id": "g1", "grant_id": "grant-1"}, "admin")

    store.kv_put("execution-profiles/prof-1", __import__("json").dumps({
        "profile_id": "prof-1", "group_id": "g1", "name": "standard",
        "version": "1.0.0", "agent_runtime_id": "rt-1", "status": "ACTIVE",
        "skill_bundle_ids": [], "mcp_grant_ids": [], "resource_budget": {},
    }))

    store.kv_put("instances/inst-1", __import__("json").dumps({
        "instance_id": "inst-1", "execution_profile_id": "prof-1",
        "run_workspace_id": "ws-1", "current_attempt_id": "attempt-1",
    }))

    return preflight, runtime_svc, mcp_svc, manifest_svc, store


def test_preflight_all_pass(env):
    preflight, _, _, manifest_svc, _ = env
    result = preflight.run("inst-1")
    assert result["status"] == "PASSED"
    assert result["manifest_id"] is not None
    assert all(c["status"] == "PASS" for c in result["checks"])


def test_preflight_runtime_unhealthy(env):
    preflight, runtime_svc, _, _, _ = env
    runtime_svc.update_health("rt-1", "UNHEALTHY")
    result = preflight.run("inst-1")
    assert result["status"] == "BLOCKED"
    runtime_check = next(c for c in result["checks"] if c["check_id"].startswith("agent-runtime"))
    assert runtime_check["status"] == "FAIL"
    assert "remediation" in runtime_check


def test_preflight_checks_each_node_provider(env):
    """DAG 节点绑定不同 provider 时，Preflight 应逐一检查对应 Runtime。"""
    import json as _json
    preflight, runtime_svc, _, _, store = env
    runtime_svc.register({"runtime_id": "acp-claude", "name": "ACP claude"}, "daemon")
    runtime_svc.register({"runtime_id": "acp-codex", "name": "ACP codex"}, "daemon")
    runtime_svc.update_health("acp-claude", "HEALTHY")

    # 实例指向模板版本，节点分别使用 claude 和 codex
    store.kv_put("instances/inst-1", _json.dumps({
        "instance_id": "inst-1", "execution_profile_id": "prof-1",
        "run_workspace_id": "ws-1", "current_attempt_id": "attempt-1",
        "template_id": "tpl-1", "version_id": "v1",
    }))
    store.kv_put("jobflows/templates/tpl-1/versions/v1/manifest", _json.dumps({
        "tasks": [
            {"id": "design", "agent": "claude", "depends_on": []},
            {"id": "build", "agent": "codex", "depends_on": ["design"]},
        ],
    }))

    # codex Runtime 不健康 → 应 BLOCKED，且 claude 节点的 Runtime 检查仍 PASS
    runtime_svc.update_health("acp-codex", "UNHEALTHY")
    result = preflight.run("inst-1")
    assert result["status"] == "BLOCKED"
    by_id = {c["check_id"]: c for c in result["checks"]}
    assert by_id["agent-runtime:acp-claude"]["status"] == "PASS"
    assert by_id["agent-runtime:acp-codex"]["status"] == "FAIL"
    assert "codex" in by_id["agent-runtime:acp-codex"]["detail"]
