from __future__ import annotations

import json

import pytest

from harness_framework.versioning import VersionConflict, VersionedResourceStore
from harness_framework.local_store import LocalStore
from tests.conftest import MockConsulStore


def test_resources_advance_independently_and_preserve_immutable_documents():
    store = MockConsulStore()
    versions = VersionedResourceStore(store)

    requirement_v1 = versions.publish(
        "req-1", "requirement", {"title": "one"}, actor="alice"
    )
    dag_v1 = versions.publish("req-1", "dag", {"a": []}, actor="alice")
    requirement_v2 = versions.publish(
        "req-1", "requirement", {"title": "two"},
        actor="bob", expected_revision=1,
    )

    assert requirement_v1.revision == 1
    assert requirement_v2.revision == 2
    assert dag_v1.revision == 1
    old_raw, _ = store.kv_get(
        "workflows/req-1/versions/requirement/revisions/"
        f"{requirement_v1.version_id}/document"
    )
    assert json.loads(old_raw)["title"] == "one"
    current, metadata = versions.get_current("req-1", "requirement")
    assert current == {"title": "two"}
    assert metadata == requirement_v2


def test_expected_revision_conflict_does_not_move_pointer():
    store = MockConsulStore()
    versions = VersionedResourceStore(store)
    first = versions.publish("req-1", "plan", {"steps": []}, actor="alice")

    with pytest.raises(VersionConflict, match="expected plan revision"):
        versions.publish(
            "req-1", "plan", {"steps": ["late"]},
            actor="bob", expected_revision=0,
        )

    current, metadata = versions.get_current("req-1", "plan")
    assert current == {"steps": []}
    assert metadata == first


def test_unknown_resource_kind_is_rejected():
    with pytest.raises(ValueError, match="unsupported"):
        VersionedResourceStore(MockConsulStore()).publish(
            "req-1", "unknown", {}, actor="alice"
        )


def test_first_publication_uses_create_cas_with_local_store():
    versions = VersionedResourceStore(LocalStore())
    published = versions.publish(
        "req-local", "requirement", {"title": "safe"}, actor="alice"
    )
    assert published.revision == 1
