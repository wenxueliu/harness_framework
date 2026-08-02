from __future__ import annotations

import pytest

from harness_framework.recovery import (
    RecoveryPolicy, rewind_to_task, select_recovery_path, task_ancestors,
    validate_recovery_target,
)
from tests.conftest import MockConsulStore


def _failure(**overrides):
    value = {"failure_type": "HARD", "severity": "HIGH", "retryable": True}
    value.update(overrides)
    return value


def test_recovery_advances_through_all_four_paths():
    policy = RecoveryPolicy.from_dict({
        "primary_attempts": 1, "narrowed_attempts": 2, "degraded_attempts": 1,
        "human_target": "incident-commander",
    })
    assert select_recovery_path(policy, _failure(), 0).path == "PRIMARY"
    assert select_recovery_path(policy, _failure(), 1).path == "NARROWED"
    assert select_recovery_path(policy, _failure(), 2).path == "NARROWED"
    assert select_recovery_path(policy, _failure(), 3).path == "DEGRADED"
    final = select_recovery_path(policy, _failure(), 4)
    assert final.path == "HUMAN"
    assert final.escalation_target == "incident-commander"


def test_critical_and_non_retryable_failures_escalate_immediately():
    policy = RecoveryPolicy()
    assert select_recovery_path(
        policy, _failure(severity="CRITICAL"), 0
    ).reason == "critical_failure"
    assert select_recovery_path(
        policy, _failure(retryable=False), 0
    ).reason == "non_retryable_failure"


def test_partial_non_retryable_failure_can_take_compensating_paths():
    decision = select_recovery_path(
        RecoveryPolicy(), _failure(failure_type="PARTIAL", retryable=False), 0
    )
    assert decision.path == "PRIMARY"


@pytest.mark.parametrize("value", [
    {"primary_attempts": -1}, {"narrowed_action": ""}, {"unknown": True},
])
def test_invalid_recovery_policy_is_rejected(value):
    with pytest.raises(ValueError):
        RecoveryPolicy.from_dict(value)


def test_recovery_target_must_be_current_task_or_ancestor():
    dag = {
        "design": {"depends_on": []},
        "build": {"depends_on": ["design"]},
        "review": {"depends_on": ["build"]},
        "unrelated": {"depends_on": []},
    }
    assert task_ancestors(dag, "review") == {"design", "build", "review"}
    validate_recovery_target(dag, "review", "design", ["design", "build"])
    with pytest.raises(ValueError, match="not the current task or an ancestor"):
        validate_recovery_target(dag, "review", "unrelated")
    with pytest.raises(ValueError, match="not allowed"):
        validate_recovery_target(dag, "review", "design", ["build"])


def test_rewind_invalidates_target_downstream_and_delivers_feedback():
    dag = {
        "design": {"depends_on": []},
        "build": {"depends_on": ["design"]},
        "review": {"depends_on": ["build"]},
    }
    initial = {"workflows/req-1/dependencies": __import__("json").dumps(dag)}
    for task in dag:
        initial[f"workflows/req-1/tasks/{task}/status"] = "DONE"
        initial[f"workflows/req-1/tasks/{task}/attempt_id"] = f"attempt-{task}"
    store = MockConsulStore(initial)
    result = rewind_to_task(
        store, "req-1", "review", "design", {"summary": "fix spec"},
        actor="review-agent", allowed_targets=["design", "build", "review"],
    )
    assert result["impacted_tasks"] == ["build", "design", "review"]
    assert store.kv_get("workflows/req-1/tasks/design/status")[0] == "PENDING"
    assert store.kv_get("workflows/req-1/tasks/build/status")[0] == "BLOCKED"
    assert store.kv_get("workflows/req-1/tasks/review/status")[0] == "BLOCKED"
    raw = store.kv_get(
        "workflows/req-1/tasks/design/recovery_feedback/current"
    )[0]
    assert __import__("json").loads(raw)["feedback"]["summary"] == "fix spec"
