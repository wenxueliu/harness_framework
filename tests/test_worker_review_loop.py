from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / "skills" / "stage-bridge" / "scripts" / "worker.py"
)
SPEC = importlib.util.spec_from_file_location("stage_bridge_worker_review", SCRIPT)
WORKER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(WORKER)


def _quiet_worker(monkeypatch):
    writes = {}
    monkeypatch.setattr(WORKER, "kv_put", lambda key, value, **_: writes.__setitem__(key, value) or True)
    monkeypatch.setattr(WORKER, "kv_get", lambda *_args, **_kwargs: (None, 1))
    monkeypatch.setattr(WORKER, "log_step", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(WORKER, "record_session_start", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(WORKER, "record_session_end", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(WORKER, "get_current_run", lambda _req_id: "run-1")
    return writes


def test_executor_receives_review_feedback_until_reviewer_passes(monkeypatch):
    writes = _quiet_worker(monkeypatch)
    calls = []
    review_results = iter([
        {
            "verdict": "CHANGES_REQUIRED",
            "summary": "fix race",
            "reviewer": "review-agent",
            "findings": [{"id": "R-1", "blocking": True}],
        },
        {
            "verdict": "PASS",
            "summary": "looks good",
            "reviewer": "review-agent",
        },
    ])

    def run(command, payload, _timeout):
        calls.append((command[0], payload))
        if command[0] == "review":
            return next(review_results)
        return {"status": "DONE", "artifact_refs": [f"commit-{payload['round']}"]}

    monkeypatch.setattr(WORKER, "_run_json_command", run)
    result = WORKER.execute_task(
        "req-1", "api", {
            "description": "implement API",
            "review_policy": json.dumps({
                "max_rounds": 3,
                "dimensions": ["correctness"],
            }),
            "completion_contract": json.dumps({"required_gates": ["review"]}),
        }, {}, {
            "agent_id": "exec-agent",
            "executor": ["exec"],
            "reviewer": ["review"],
        },
    )

    assert result["status"] == "DONE"
    assert result["review_rounds"] == 2
    executor_calls = [payload for name, payload in calls if name == "exec"]
    assert executor_calls[0]["review_feedback"] is None
    assert executor_calls[1]["review_feedback"]["summary"] == "fix race"
    assert writes["workflows/req-1/tasks/api/evidence/review/verdict"] == "PASS"


def test_independent_reviewer_cannot_be_executor(monkeypatch):
    _quiet_worker(monkeypatch)

    def run(command, _payload, _timeout):
        if command[0] == "review":
            return {"verdict": "PASS", "reviewer": "same-agent"}
        return {"status": "DONE"}

    monkeypatch.setattr(WORKER, "_run_json_command", run)
    result = WORKER.execute_task(
        "req-1", "api", {
            "review_policy": json.dumps({"require_independent_agent": True}),
            "completion_contract": json.dumps({"required_gates": ["review"]}),
        }, {}, {
            "agent_id": "same-agent",
            "executor": ["exec"],
            "reviewer": ["review"],
        },
    )
    assert result["status"] == "FAILED"
    assert "cannot review its own task" in result["error"]


def test_review_pass_can_request_human_approval(monkeypatch):
    _quiet_worker(monkeypatch)

    def run(command, _payload, _timeout):
        if command[0] == "review":
            return {"verdict": "PASS", "reviewer": "review-agent"}
        return {"status": "DONE"}

    monkeypatch.setattr(WORKER, "_run_json_command", run)
    result = WORKER.execute_task(
        "req-1", "api", {
            "review_policy": json.dumps({"human_approval_after_pass": True}),
            "completion_contract": json.dumps({"required_gates": ["review"]}),
        }, {}, {
            "agent_id": "exec-agent",
            "executor": ["exec"],
            "reviewer": ["review"],
        },
    )
    assert result["status"] == "DONE"
    assert result["human_approval_required"] is True


def test_reviewer_can_request_rewind_to_allowed_upstream(monkeypatch):
    _quiet_worker(monkeypatch)

    def run(command, _payload, _timeout):
        if command[0] == "review":
            return {
                "verdict": "CHANGES_REQUIRED",
                "reviewer": "review-agent",
                "summary": "design is incomplete",
                "recovery_target": "design",
            }
        return {"status": "DONE"}

    monkeypatch.setattr(WORKER, "_run_json_command", run)
    result = WORKER.execute_task(
        "req-1", "api", {
            "review_policy": json.dumps({
                "allowed_recovery_targets": ["api", "design"],
                "default_recovery_target": "api",
            }),
            "completion_contract": json.dumps({"required_gates": ["review"]}),
        }, {}, {
            "agent_id": "exec-agent",
            "executor": ["exec"],
            "reviewer": ["review"],
        },
    )
    assert result["status"] == "REWIND_REQUIRED"
    assert result["target_task"] == "design"


def test_reviewer_cannot_select_disallowed_recovery_target(monkeypatch):
    _quiet_worker(monkeypatch)

    def run(command, _payload, _timeout):
        if command[0] == "review":
            return {
                "verdict": "CHANGES_REQUIRED",
                "reviewer": "review-agent",
                "recovery_target": "unrelated",
            }
        return {"status": "DONE"}

    monkeypatch.setattr(WORKER, "_run_json_command", run)
    result = WORKER.execute_task(
        "req-1", "api", {
            "review_policy": json.dumps({
                "allowed_recovery_targets": ["api", "design"],
            }),
            "completion_contract": json.dumps({"required_gates": ["review"]}),
        }, {}, {
            "agent_id": "exec-agent",
            "executor": ["exec"],
            "reviewer": ["review"],
        },
    )
    assert result["status"] == "FAILED"
    assert "disallowed recovery target" in result["error"]
