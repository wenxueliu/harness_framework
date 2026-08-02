from __future__ import annotations

import json

import pytest

from harness_framework.local_store import LocalStore
from harness_framework.side_effects import IdempotencyConflict, SideEffectLedger


def _ledger():
    store = LocalStore()
    source = "workflows/req-1/tasks/deploy"
    compensation = "workflows/req-1/tasks/rollback"
    store.kv_put(f"{source}/attempt_id", "attempt-deploy")
    store.kv_put(f"{source}/lease_epoch", "2")
    store.kv_put(f"{source}/compensation_task", "rollback")
    store.kv_put(f"{compensation}/status", "BLOCKED")
    store.kv_put(f"{compensation}/attempt_id", "attempt-rollback")
    store.kv_put(f"{compensation}/lease_epoch", "1")
    return SideEffectLedger(store), store


def test_completed_idempotency_key_replays_result_without_reexecution():
    ledger, _ = _ledger()
    begun = ledger.begin(
        "req-1", "deploy", "payment:42", attempt_id="attempt-deploy", lease_epoch=2
    )
    completed = ledger.complete(
        "req-1", "deploy", "payment:42", attempt_id="attempt-deploy",
        lease_epoch=2, result={"transaction_id": "tx-1"},
    )
    replay = ledger.begin(
        "req-1", "deploy", "payment:42", attempt_id="attempt-deploy", lease_epoch=2
    )
    assert begun["action"] == "EXECUTE"
    assert completed["status"] == "COMPLETED"
    assert replay["action"] == "REPLAY"
    assert replay["record"]["result"]["transaction_id"] == "tx-1"


def test_failed_side_effect_activates_compensation_only_task():
    ledger, store = _ledger()
    ledger.begin(
        "req-1", "deploy", "release:7", attempt_id="attempt-deploy", lease_epoch=2
    )
    failed = ledger.fail_and_compensate(
        "req-1", "deploy", "release:7", attempt_id="attempt-deploy",
        lease_epoch=2, error="partial rollout",
    )
    assert failed["status"] == "FAILED"
    assert failed["compensation_task"] == "rollback"
    assert store.kv_get("workflows/req-1/tasks/rollback/status")[0] == "PENDING"
    assert store.kv_get("workflows/req-1/tasks/rollback/idempotency_key")[0] == "release:7"

    compensated = ledger.mark_compensated(
        "req-1", "deploy", "release:7",
        compensation_attempt_id="attempt-rollback", compensation_lease_epoch=1,
        compensation_task="rollback",
    )
    assert compensated["status"] == "COMPENSATED"


def test_stale_attempt_is_fenced_before_side_effect():
    ledger, _ = _ledger()
    with pytest.raises(PermissionError, match="fenced"):
        ledger.begin(
            "req-1", "deploy", "key", attempt_id="attempt-old", lease_epoch=1
        )


def test_in_progress_key_cannot_be_taken_by_new_attempt():
    ledger, store = _ledger()
    ledger.begin(
        "req-1", "deploy", "key", attempt_id="attempt-deploy", lease_epoch=2
    )
    store.kv_put("workflows/req-1/tasks/deploy/attempt_id", "attempt-new")
    store.kv_put("workflows/req-1/tasks/deploy/lease_epoch", "3")
    with pytest.raises(IdempotencyConflict, match="another attempt"):
        ledger.begin(
            "req-1", "deploy", "key", attempt_id="attempt-new", lease_epoch=3
        )
