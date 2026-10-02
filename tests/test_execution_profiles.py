from __future__ import annotations

import json

import pytest

from harness_framework.execution_profiles import ExecutionProfileService, ProfileError
from harness_framework.local_store import LocalStore


@pytest.fixture()
def service():
    return ExecutionProfileService(LocalStore())


def _body(**overrides):
    base = {
        "profile_id": "prof-001",
        "group_id": "g1",
        "name": "standard-dev",
        "version": "1.0.0",
        "agent_runtime_id": "rt-claude-code",
        "resource_budget": {"max_cost_usd": 10.0},
        "skill_bundle_ids": ["sb-tdd"],
        "mcp_grant_ids": ["grant-fs"],
    }
    base.update(overrides)
    return base


def test_create_profile(service):
    record = service.create(_body(), actor="admin")
    assert record["profile_id"] == "prof-001"
    assert record["status"] == "ACTIVE"
    assert record["created_by"] == "admin"


def test_create_missing_group_raises(service):
    with pytest.raises(ProfileError, match="GROUP_ID_REQUIRED"):
        service.create(_body(group_id=""), actor="admin")


def test_get_not_found(service):
    with pytest.raises(ProfileError, match="PROFILE_NOT_FOUND"):
        service.get("nonexistent")


def test_list_for_group(service):
    service.create(_body(), actor="admin")
    service.create(_body(profile_id="prof-002", name="strict"), actor="admin")
    results = service.list_for_group("g1")
    assert len(results) == 2


def test_update_success(service):
    service.create(_body(), actor="admin")
    record = service.update("prof-001", {"expected_revision": 1, "name": "updated"}, actor="admin")
    assert record["name"] == "updated"


def test_update_conflict(service):
    service.create(_body(), actor="admin")
    with pytest.raises(ProfileError, match="REVISION_CONFLICT"):
        service.update("prof-001", {"expected_revision": 99, "name": "x"}, actor="admin")


def test_disable(service):
    service.create(_body(), actor="admin")
    record = service.disable("prof-001", actor="admin")
    assert record["status"] == "DISABLED"
