from __future__ import annotations

import pytest

from harness_framework.recovery import RecoveryPolicy, select_recovery_path


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
