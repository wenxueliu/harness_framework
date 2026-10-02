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
