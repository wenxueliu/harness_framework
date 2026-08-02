"""Attempt ownership and fencing invariants."""
from __future__ import annotations

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
