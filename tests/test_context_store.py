from __future__ import annotations

import pytest

from harness_framework.context_store import ContextStore
from harness_framework.local_store import LocalStore


def test_immutable_facts_cannot_be_overwritten():
    context = ContextStore(LocalStore())
    context.put_fact("req-1", "customer/id", "c-1", actor="planner")
    with pytest.raises(ValueError, match="immutable fact"):
        context.put_fact("req-1", "customer/id", "c-2", actor="planner")


def test_artifacts_are_versioned_without_overwriting_old_value():
    store = LocalStore()
    context = ContextStore(store)
    first = context.publish_artifact("req-1", "spec", {"v": 1}, actor="designer")
    second = context.publish_artifact(
        "req-1", "spec", {"v": 2}, actor="designer",
        lineage=[first["version_id"]],
    )
    assert first["revision"] == 1
    assert second["revision"] == 2
    old, _ = store.kv_get(
        "workflows/req-1/knowledge/artifacts/spec/versions/"
        f"{first['version_id']}/value"
    )
    assert old == '{"v":1}'


def test_working_memory_is_task_scoped_and_events_are_append_only():
    context = ContextStore(LocalStore())
    memory_path = context.put_working_memory(
        "req-1", "api", "scratch", [1], actor="worker"
    )
    event_one = context.append_event(
        "req-1", "api", {"message": "started"}, actor="worker"
    )
    event_two = context.append_event(
        "req-1", "api", {"message": "finished"}, actor="worker"
    )
    assert "/working_memory/api/" in memory_path
    assert event_one != event_two


def test_summary_requires_lineage_and_restricted_reads_require_authorization():
    context = ContextStore(LocalStore())
    with pytest.raises(ValueError, match="source_refs"):
        context.put_summary(
            "req-1", "daily", {"id": "x"}, actor="agent", source_refs=[],
            full_value={"id": "x"}, preserved_fields=["id"], max_bytes=100,
        )
    context.put_restricted(
        "req-1", "credentials", {"token": "redacted"},
        actor="owner", classification="SECRET",
    )
    with pytest.raises(PermissionError, match="authorization"):
        context.read_namespace("req-1", "restricted")
    values = context.read_namespace("req-1", "restricted", allow_restricted=True)
    assert values["credentials"]["classification"] == "SECRET"


def test_bounded_summary_preserves_mandatory_nested_fields():
    store = LocalStore()
    context = ContextStore(store)
    full = {
        "requirement_id": "REQ-1",
        "constraints": {"region": "CN", "retention_days": 30},
        "long_body": "x" * 1000,
    }
    summary = {
        "requirement_id": "REQ-1",
        "constraints": {"region": "CN"},
        "overview": "bounded",
    }
    path = context.put_summary(
        "req-1", "requirement", summary, actor="summarizer",
        source_refs=["artifacts/requirement/v1"], full_value=full,
        preserved_fields=["requirement_id", "constraints.region"], max_bytes=200,
    )
    record = context.read_namespace("req-1", "summaries")["requirement"]
    assert path.endswith("/summaries/requirement")
    assert record["value"] == summary
    assert record["size_bytes"] <= 200
    assert record["preserved_fields"] == ["requirement_id", "constraints.region"]


def test_bounded_summary_rejects_field_loss_and_oversize_values():
    context = ContextStore(LocalStore())
    with pytest.raises(ValueError, match="did not preserve"):
        context.put_summary(
            "req-1", "bad", {"id": "wrong"}, actor="agent",
            source_refs=["artifact/v1"], full_value={"id": "right"},
            preserved_fields=["id"], max_bytes=100,
        )
    with pytest.raises(ValueError, match="exceeds max_bytes"):
        context.put_summary(
            "req-1", "large", {"id": "right", "text": "x" * 100},
            actor="agent", source_refs=["artifact/v1"],
            full_value={"id": "right"}, preserved_fields=["id"], max_bytes=20,
        )


def test_invalid_namespace_and_path_traversal_are_rejected():
    context = ContextStore(LocalStore())
    with pytest.raises(ValueError, match="unknown context namespace"):
        context.read_namespace("req-1", "misc")
    with pytest.raises(ValueError, match="invalid path segment"):
        context.put_fact("req-1", "../secret", "x", actor="planner")
