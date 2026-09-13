from __future__ import annotations

import pytest

from harness_framework.api_errors import ConflictError, ValidationError
from harness_framework.local_store import LocalStore
from harness_framework.project_groups import ProjectGroupService, UNASSIGNED_GROUP_ID


@pytest.fixture
def service():
    return ProjectGroupService(LocalStore())


def test_create_group_establishes_owner_and_lists_virtual_unassigned(service):
    group = service.create(name="Core", description="team", actor="user:alice")
    members = service.list_members(group["group_id"])
    listed = service.list()["project_groups"]

    assert members[0]["role"] == "OWNER"
    assert members[0]["subject_id"] == "user:alice"
    assert listed[0]["group_id"] == UNASSIGNED_GROUP_ID
    assert any(item["group_id"] == group["group_id"] for item in listed)


def test_group_update_uses_revision_and_archive_is_one_way(service):
    group = service.create(name="Core", description="", actor="user:alice")
    updated = service.update(
        group["group_id"], expected_revision=1, changes={"name": "Platform"}
    )
    assert updated["revision"] == 2
    with pytest.raises(ConflictError):
        service.update(group["group_id"], expected_revision=1, changes={"name": "Old"})
    archived = service.update(
        group["group_id"], expected_revision=2, changes={"status": "ARCHIVED"}
    )
    assert archived["status"] == "ARCHIVED"
    with pytest.raises(ValidationError, match="只允许归档"):
        service.update(
            group["group_id"], expected_revision=3, changes={"status": "ACTIVE"}
        )


def test_last_owner_cannot_be_removed(service):
    group = service.create(name="Core", description="", actor="user:alice")
    with pytest.raises(ConflictError, match="最后一个 Owner"):
        service.delete_member(group["group_id"], "user:alice")


def test_workflow_has_one_primary_group_and_other_group_can_reference(service):
    first = service.create(name="First", description="", actor="user:alice")
    second = service.create(name="Second", description="", actor="user:bob")
    service.store.kv_put("workflows/req-1/dependencies", "{}")
    service.assign_primary_workflow(first["group_id"], "req-1")

    with pytest.raises(ConflictError, match="主项目组"):
        service.assign_primary_workflow(second["group_id"], "req-1")
    reference = service.add_reference(second["group_id"], "req-1", ["run:create"])

    assert reference["primary_group_id"] == first["group_id"]
    assert service.list_workflows(first["group_id"])[0]["relationship"] == "primary"
    assert service.list_workflows(second["group_id"])[0]["relationship"] == "reference"


def test_unassigned_only_contains_legacy_workflows(service):
    group = service.create(name="Core", description="", actor="user:alice")
    service.store.kv_put("workflows/legacy/dependencies", "{}")
    service.store.kv_put("workflows/owned/dependencies", "{}")
    service.assign_primary_workflow(group["group_id"], "owned")

    assert service.list_workflows(UNASSIGNED_GROUP_ID) == [
        {"req_id": "legacy", "relationship": "unassigned"}
    ]
