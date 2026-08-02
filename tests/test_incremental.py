from __future__ import annotations

import json

import pytest

from harness_framework.incremental import (
    affected_downstream_closure, invalidate_impacted_tasks,
)
from tests.conftest import MockConsulStore


DAG = {
    "design": {"depends_on": []},
    "api": {"depends_on": ["design"]},
    "docs": {"depends_on": ["design"]},
    "test": {"depends_on": ["api"]},
    "deploy": {"depends_on": ["test", "docs"]},
}


def test_affected_closure_includes_only_transitive_downstream_tasks():
    assert affected_downstream_closure(DAG, ["api"]) == {"api", "test", "deploy"}
    with pytest.raises(ValueError, match="unknown changed tasks"):
        affected_downstream_closure(DAG, ["missing"])


def test_invalidation_archives_outputs_and_preserves_unaffected_tasks():
    initial = {}
    for task in DAG:
        initial[f"workflows/req-1/tasks/{task}/status"] = "DONE"
    initial.update({
        "workflows/req-1/tasks/api/artifacts/code/current_version": "2",
        "workflows/req-1/tasks/api/evidence/tests/verdict": "PASS",
        "workflows/req-1/tasks/api/attempt_id": "attempt-old",
        "workflows/req-1/tasks/docs/artifacts/site/current_version": "1",
    })
    store = MockConsulStore(initial)

    impacted = invalidate_impacted_tasks(
        store, "req-1", DAG, ["api"], change_id="chg-1"
    )

    assert impacted == {"api", "test", "deploy"}
    api_status, _ = store.kv_get("workflows/req-1/tasks/api/status")
    test_status, _ = store.kv_get("workflows/req-1/tasks/test/status")
    docs_status, _ = store.kv_get("workflows/req-1/tasks/docs/status")
    assert api_status == "PENDING"
    assert test_status == "BLOCKED"
    assert docs_status == "DONE"
    assert store.kv_get("workflows/req-1/tasks/api/attempt_id")[0] is None
    assert store.kv_get(
        "workflows/req-1/invalidations/chg-1/tasks/api/attempt/attempt_id"
    )[0] == "attempt-old"
    assert store.kv_get(
        "workflows/req-1/tasks/docs/artifacts/site/current_version"
    )[0] == "1"
    affected_raw, _ = store.kv_get(
        "workflows/req-1/invalidations/chg-1/affected_tasks"
    )
    assert json.loads(affected_raw) == ["api", "deploy", "test"]
