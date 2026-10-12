import pytest
from harness_framework.agent_runtimes import AgentRuntimeService, RuntimeError
from harness_framework.local_store import LocalStore

@pytest.fixture()
def svc():
    return AgentRuntimeService(LocalStore())

def test_register(svc):
    r = svc.register({"runtime_id": "rt-1", "name": "claude-code"}, "admin")
    assert r["runtime_id"] == "rt-1"
    assert r["status"] == "OFFLINE"

def test_get_not_found(svc):
    with pytest.raises(RuntimeError, match="RUNTIME_NOT_FOUND"):
        svc.get("nonexistent")

def test_health_update(svc):
    svc.register({"runtime_id": "rt-1", "name": "claude-code"}, "admin")
    r = svc.update_health("rt-1", "HEALTHY")
    assert r["status"] == "HEALTHY"


def test_derive_adapter_id():
    from harness_framework.agent_runtimes import derive_adapter_id
    assert derive_adapter_id(["npx", "-y", "@agentclientprotocol/claude-agent-acp"]) == "claude-agent-acp"
    assert derive_adapter_id(["python", "-m", "codex_acp"]) == "codex_acp"
    assert derive_adapter_id(["/usr/local/bin/my-adapter", "--flag"]) == "my-adapter"
    assert derive_adapter_id([]) == ""


def test_ensure_daemon_runtime_creates_and_updates():
    from harness_framework.agent_runtimes import ensure_daemon_runtime
    store = LocalStore()
    argv = ["npx", "-y", "@agentclientprotocol/claude-agent-acp"]

    record = ensure_daemon_runtime(store, "claude", argv)
    assert record["runtime_id"] == "acp-claude"
    assert record["adapter_id"] == "claude-agent-acp"
    assert record["status"] == "HEALTHY"
    assert record["registered_by"] == "daemon"
    assert record["command"] == argv

    # 人工标记 OFFLINE 后，活跃 dispatcher 心跳应覆盖回 HEALTHY 并刷新 adapter
    svc = AgentRuntimeService(store)
    svc.update_health("acp-claude", "OFFLINE")
    updated = ensure_daemon_runtime(store, "claude", ["python", "-m", "codex_acp"])
    assert updated["status"] == "HEALTHY"
    assert updated["adapter_id"] == "codex_acp"
    assert updated["registered_at"] == record["registered_at"]  # 不重建，保留首次注册时间


def test_dispatcher_claim_heartbeats_runtime(mock_consul):
    """dispatcher 认领成功后应回写 acp-<provider> Runtime 心跳。"""
    import json as _json
    from harness_framework.acp_dispatcher import ACPDispatcher
    from harness_framework.agent_runtimes import AgentRuntimeService
    from harness_framework.run_manager import RunManager

    store = mock_consul
    deps = {
        "design": {"type": "design", "depends_on": [], "agent_name": "claude"},
    }
    store.kv_put("workflows/req-rt/dependencies", _json.dumps(deps))
    store.kv_put("workflows/req-rt/published", "true")
    store.kv_put("workflows/req-rt/tasks/design/status", "PENDING")

    dispatcher = ACPDispatcher(
        store, RunManager(store),
        commands={"claude": ["npx", "-y", "@agentclientprotocol/claude-agent-acp"]},
        client_factory=lambda *a, **k: None,
    )
    claim = dispatcher._claim("req-rt", "design", {"type": "design", "acp": _json.dumps({"agent": "claude"})})
    assert claim is not None

    record = AgentRuntimeService(store).get("acp-claude")
    assert record["status"] == "HEALTHY"
    assert record["adapter_id"] == "claude-agent-acp"
