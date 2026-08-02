from __future__ import annotations

import json

import pytest

from harness_framework.budgets import BudgetLedger, ResourceBudget
from harness_framework.local_store import LocalStore


def _ledger():
    store = LocalStore()
    base = "workflows/req-1/tasks/task"
    store.kv_put(f"{base}/attempt_id", "attempt-1")
    store.kv_put(f"{base}/lease_epoch", "2")
    store.kv_put(f"{base}/resource_budget", json.dumps({
        "max_tokens": 100, "max_cost_usd": 1.5,
        "max_tool_calls": 3, "max_wall_clock_seconds": 60,
    }))
    return BudgetLedger(store), store


def test_usage_accumulates_until_budget_is_exceeded():
    ledger, store = _ledger()
    first = ledger.consume(
        "req-1", "task", attempt_id="attempt-1", lease_epoch=2,
        tokens=40, cost_usd=0.5, tool_calls=1, wall_clock_seconds=20,
    )
    second = ledger.consume(
        "req-1", "task", attempt_id="attempt-1", lease_epoch=2,
        tokens=70, cost_usd=0.2, tool_calls=1, wall_clock_seconds=10,
    )
    assert first["status"] == "ALLOW"
    assert second["status"] == "TRIPPED"
    assert second["exceeded"] == ["max_tokens"]
    breaker = json.loads(store.kv_get(
        "workflows/req-1/tasks/task/budget/circuit_breaker"
    )[0])
    assert breaker["status"] == "OPEN"


def test_stale_attempt_cannot_write_usage():
    ledger, _ = _ledger()
    with pytest.raises(PermissionError, match="fenced"):
        ledger.consume(
            "req-1", "task", attempt_id="attempt-old", lease_epoch=1, tokens=1
        )


@pytest.mark.parametrize("value", [
    {"max_tokens": 0}, {"max_cost_usd": -1}, {"max_tool_calls": True},
    {"unknown": 1},
])
def test_invalid_budget_is_rejected(value):
    with pytest.raises(ValueError):
        ResourceBudget.from_dict(value)
