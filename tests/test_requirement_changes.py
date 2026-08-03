from __future__ import annotations

import json

import pytest

from harness_framework.requirement_changes import RequirementChangeService
from harness_framework.run_manager import RunManager
from harness_framework.versioning import VersionedResourceStore
from tests.conftest import MockConsulStore


DAG = {
    "design": {"depends_on": []},
    "api": {"depends_on": ["design"]},
    "docs": {"depends_on": ["design"]},
    "test": {"depends_on": ["api"]},
    "deploy": {"depends_on": ["test", "docs"]},
}


def _running_workflow() -> tuple[MockConsulStore, str]:
    store = MockConsulStore()
    versions = VersionedResourceStore(store)
    versions.publish(
        "req-1", "requirement", {"content": "requirement v1"}, actor="setup"
    )
    versions.publish("req-1", "dag", DAG, actor="setup")
    versions.publish("req-1", "workflow_spec", {"tasks": DAG}, actor="setup")
    versions.publish("req-1", "plan", {"tasks": list(DAG)}, actor="setup")
    store.kv_put("workflows/req-1/dependencies", json.dumps(DAG))
    for task in DAG:
        store.kv_put(f"workflows/req-1/tasks/{task}/status", "DONE")
    store.kv_put("workflows/req-1/tasks/api/attempt_id", "attempt-old")
    store.kv_put("workflows/req-1/tasks/api/artifacts/code/current", "commit-old")
    run_id = RunManager(store).get_or_create_run("req-1", actor="setup")
    return store, run_id


def test_change_reuses_workflow_rolls_run_and_only_invalidates_closure():
    store, old_run = _running_workflow()
    store.kv_put("workflows/req-1/tasks/docs/status", "IN_PROGRESS")
    store.kv_put("workflows/req-1/tasks/docs/attempt_id", "docs-active")

    result = RequirementChangeService(store).apply(
        "req-1", content="requirement v2", reason="change API behavior",
        changed_tasks=["api"], actor="alice",
    )

    assert result["req_id"] == "req-1"
    assert result["old_run_id"] == old_run
    assert result["new_run_id"] != old_run
    assert result["affected_tasks"] == ["api", "deploy", "test"]
    assert store.kv_get(f"workflows/req-1/runs/{old_run}/status")[0] == "SUPERSEDED"
    assert store.kv_get("workflows/req-1/current_run")[0] == result["new_run_id"]
    assert store.kv_get("workflows/req-1/tasks/api/status")[0] == "PENDING"
    assert store.kv_get("workflows/req-1/tasks/test/status")[0] == "BLOCKED"
    assert store.kv_get("workflows/req-1/tasks/docs/status")[0] == "IN_PROGRESS"
    assert store.kv_get("workflows/req-1/tasks/docs/attempt_id")[0] == "docs-active"
    assert store.kv_get("workflows/req-1/tasks/api/attempt_id")[0] is None
    assert store.kv_get(
        f"workflows/req-1/invalidations/{result['change_id']}/tasks/api/attempt/attempt_id"
    )[0] == "attempt-old"

    requirement, version = VersionedResourceStore(store).get_current(
        "req-1", "requirement"
    )
    assert requirement["content"] == "requirement v2"
    assert requirement["reason"] == "change API behavior"
    assert version is not None and version.revision == 2
    assert store.kv_get("workflows/req-1/requirement")[0] == "requirement v2"
    assert store.kv_get("workflows/req-1/requirement_version")[0] == "2"


def test_text_only_change_keeps_current_run_and_task_states():
    store, old_run = _running_workflow()

    result = RequirementChangeService(store).apply(
        "req-1", content="clarified wording", reason="wording only",
        changed_tasks=[], actor="alice",
    )

    assert result["affected_tasks"] == []
    assert result["old_run_id"] == old_run
    assert result["new_run_id"] == old_run
    assert store.kv_get("workflows/req-1/tasks/api/status")[0] == "DONE"
    assert store.kv_get(f"workflows/req-1/runs/{old_run}/status")[0] == "RUNNING"


def test_change_rejects_unknown_task_without_publishing_new_version():
    store, _ = _running_workflow()

    with pytest.raises(ValueError, match="unknown changed tasks"):
        RequirementChangeService(store).apply(
            "req-1", content="bad change", reason="bad",
            changed_tasks=["missing"], actor="alice",
        )

    _document, version = VersionedResourceStore(store).get_current(
        "req-1", "requirement"
    )
    assert version is not None and version.revision == 1


def test_change_record_is_kept_under_same_workflow():
    store, _ = _running_workflow()
    result = RequirementChangeService(store).apply(
        "req-1", content="requirement v2", reason="new rule",
        changed_tasks=["api"], actor="alice",
    )

    raw, _ = store.kv_get(
        f"workflows/req-1/requirement_changes/{result['change_id']}/record"
    )
    record = json.loads(raw)
    assert record["status"] == "APPLIED"
    assert record["from_revision"] == 1
    assert record["to_revision"] == 2
    assert record["changed_tasks"] == ["api"]
    assert record["affected_tasks"] == ["api", "deploy", "test"]
