"""Attempt ownership and fencing invariants."""
from __future__ import annotations

import json
import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / "skills/stage-bridge/scripts/_consul.py"
)
SPEC = importlib.util.spec_from_file_location("stage_bridge_consul", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_validate_attempt_accepts_current_owner(monkeypatch):
    values = {
        "workflows/req-1/tasks/task-1/attempt_id": "attempt-current",
        "workflows/req-1/tasks/task-1/lease_epoch": "4",
    }
    monkeypatch.setattr(
        MODULE, "kv_get", lambda key, recurse=False: (values.get(key), 1)
    )
    assert MODULE.validate_attempt(
        "req-1", "task-1", "attempt-current", "4"
    ) == (True, "")


def test_validate_attempt_fences_stale_worker(monkeypatch):
    values = {
        "workflows/req-1/tasks/task-1/attempt_id": "attempt-new",
        "workflows/req-1/tasks/task-1/lease_epoch": "5",
    }
    monkeypatch.setattr(
        MODULE, "kv_get", lambda key, recurse=False: (values.get(key), 1)
    )
    valid, reason = MODULE.validate_attempt(
        "req-1", "task-1", "attempt-old", "4"
    )
    assert valid is False
    assert "stale" in reason


def test_validate_attempt_rejects_unowned_write(monkeypatch):
    monkeypatch.setattr(MODULE, "kv_get", lambda key, recurse=False: (None, 0))
    valid, reason = MODULE.validate_attempt("req-1", "task-1", "", "")
    assert valid is False
    assert "required" in reason


def test_renew_lease_updates_current_attempt(monkeypatch):
    values = {
        "workflows/req-1/tasks/task-1/attempt_id": "attempt-current",
        "workflows/req-1/tasks/task-1/lease_epoch": "4",
        "workflows/req-1/tasks/task-1/status": "IN_PROGRESS",
        "workflows/req-1/tasks/task-1/assigned_agent": "agent-1",
    }
    writes = {}
    monkeypatch.setattr(
        MODULE, "kv_get", lambda key, recurse=False: (values.get(key), 1)
    )
    monkeypatch.setattr(
        MODULE, "kv_put", lambda key, value, cas=None: writes.setdefault(key, value) or True
    )
    ok, reason, expires_at = MODULE.renew_attempt_lease(
        "req-1", "task-1", "attempt-current", "4", 120, "agent-1"
    )
    assert ok is True
    assert reason == ""
    assert expires_at
    assert writes["workflows/req-1/tasks/task-1/lease_renewed_at"]
    assert writes["workflows/req-1/tasks/task-1/lease_expires_at"] == expires_at


def test_renew_lease_rejects_wrong_agent(monkeypatch):
    values = {
        "workflows/req-1/tasks/task-1/attempt_id": "attempt-current",
        "workflows/req-1/tasks/task-1/lease_epoch": "4",
        "workflows/req-1/tasks/task-1/status": "IN_PROGRESS",
        "workflows/req-1/tasks/task-1/assigned_agent": "agent-1",
    }
    monkeypatch.setattr(
        MODULE, "kv_get", lambda key, recurse=False: (values.get(key), 1)
    )
    ok, reason, _ = MODULE.renew_attempt_lease(
        "req-1", "task-1", "attempt-current", "4", 120, "agent-2"
    )
    assert ok is False
    assert "another agent" in reason


def test_renew_lease_is_capped_by_hard_deadline(monkeypatch):
    hard_deadline = MODULE.lease_deadline_iso(5)
    values = {
        "workflows/req-1/tasks/task-1/attempt_id": "attempt-current",
        "workflows/req-1/tasks/task-1/lease_epoch": "4",
        "workflows/req-1/tasks/task-1/status": "IN_PROGRESS",
        "workflows/req-1/tasks/task-1/assigned_agent": "agent-1",
        "workflows/req-1/tasks/task-1/hard_deadline_at": hard_deadline,
    }
    monkeypatch.setattr(
        MODULE, "kv_get", lambda key, recurse=False: (values.get(key), 1)
    )
    monkeypatch.setattr(MODULE, "kv_put", lambda *args, **kwargs: True)
    ok, _, expires_at = MODULE.renew_attempt_lease(
        "req-1", "task-1", "attempt-current", "4", 600, "agent-1"
    )
    assert ok is True
    assert expires_at <= hard_deadline


def test_completion_contract_requires_artifacts_and_passed_gates(monkeypatch):
    base = "workflows/req-1/tasks/task-1"
    values = {
        f"{base}/completion_contract": (
            '{"required_artifacts":["report"],"required_gates":["tests"]}'
        ),
        f"{base}/artifacts/report/current_version": "1",
        f"{base}/evidence/tests/verdict": "PASS",
    }
    monkeypatch.setattr(
        MODULE, "kv_get", lambda key, recurse=False: (values.get(key), 1)
    )
    assert MODULE.check_completion_contract("req-1", "task-1") == (True, [])
    values[f"{base}/evidence/tests/verdict"] = "FAIL"
    ready, missing = MODULE.check_completion_contract("req-1", "task-1")
    assert ready is False
    assert missing == ["gate:tests"]


def test_open_budget_circuit_breaker_blocks_completion_without_contract(monkeypatch):
    base = "workflows/req-1/tasks/task-1"
    values = {
        f"{base}/budget/circuit_breaker": json.dumps({"status": "OPEN"}),
    }
    monkeypatch.setattr(MODULE, "kv_get", lambda key, **kwargs: (values.get(key), 1))
    assert MODULE.check_completion_contract("req-1", "task-1") == (
        False, ["circuit_breaker:OPEN"]
    )
