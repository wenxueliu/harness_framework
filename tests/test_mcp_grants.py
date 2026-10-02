import pytest
from harness_framework.mcp_grants import MCPService, MCPError
from harness_framework.local_store import LocalStore

@pytest.fixture()
def svc():
    return MCPService(LocalStore())

def test_register_server(svc):
    r = svc.register_server({"server_id": "mcp-1", "name": "filesystem", "transport": "stdio"}, "admin")
    assert r["server_id"] == "mcp-1"

def test_create_grant(svc):
    svc.register_server({"server_id": "mcp-1", "name": "fs"}, "admin")
    g = svc.create_grant({"server_id": "mcp-1", "group_id": "g1", "expose_tools": ["read"], "invoke_tools": ["write"]}, "admin")
    assert g["status"] == "ACTIVE"

def test_revoke_grant(svc):
    svc.register_server({"server_id": "mcp-1", "name": "fs"}, "admin")
    g = svc.create_grant({"server_id": "mcp-1", "group_id": "g1"}, "admin")
    r = svc.revoke_grant("g1", g["grant_id"], "admin")
    assert r["status"] == "REVOKED"

def test_grant_missing_server(svc):
    with pytest.raises(MCPError, match="SERVER_NOT_FOUND"):
        svc.create_grant({"server_id": "nonexistent", "group_id": "g1"}, "admin")
