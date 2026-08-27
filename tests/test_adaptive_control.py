from __future__ import annotations

import json

import pytest

from harness_framework.adaptive_control import (
    AdaptiveControlError, AdaptiveControlService, RoutingBudget,
)
from harness_framework.run_manager import RunManager
from harness_framework.local_store import LocalStore


def _workflow(mock_store):
    dag = {
        "requirements": {"depends_on": []},
        "implementation": {"depends_on": ["requirements"]},
        "test": {"depends_on": ["implementation"]},
    }
    mock_store.kv_put("workflows/r1/dependencies", json.dumps(dag))
    for task, status in {
        "requirements": "DONE", "implementation": "DONE", "test": "IN_PROGRESS",
    }.items():
        mock_store.kv_put(f"workflows/r1/tasks/{task}/status", status)
    return AdaptiveControlService(mock_store, RunManager(mock_store))


def test_atomic_action_is_idempotent_and_rejects_stale_submission(mock_store):
    service = _workflow(mock_store)
    first = service.next_action("r1", "test", actor="agent", attempt_id="a1")
    assert service.next_action("r1", "test", actor="agent", attempt_id="a1") == first
    with pytest.raises(AdaptiveControlError, match="already issued"):
        service.next_action("r1", "test", actor="other", attempt_id="a2")

    check = service.submit_check(
        "r1", "test", action_id=first["action_id"],
        state_version=first["state_version"], verdict="FAIL", verifier="pytest",
        actor="agent", evidence={"failure": "assertion"},
        command={"argv": ["pytest", "-q"], "cwd": ".", "exit_code": 1,
                 "output_digest": "sha256:abc"},
    )
    assert check["verdict"] == "FAIL"
    assert mock_store._store["workflows/r1/tasks/test/validity"] == "INVALIDATED"
    with pytest.raises(AdaptiveControlError):
        service.submit_check(
            "r1", "test", action_id=first["action_id"],
            state_version=first["state_version"], verdict="PASS", verifier="pytest",
            actor="agent", evidence={},
        )


def test_check_requires_real_argv_command_evidence(mock_store):
    service = _workflow(mock_store)
    action = service.next_action("r1", "test", actor="agent")
    with pytest.raises(AdaptiveControlError, match="argv"):
        service.submit_check(
            "r1", "test", action_id=action["action_id"],
            state_version=action["state_version"], verdict="PASS", verifier="command",
            actor="agent", evidence={}, command={"argv": "pytest", "exit_code": 0},
        )


def test_dynamic_route_validates_closure_and_rolls_run_forward(mock_store):
    service = _workflow(mock_store)
    action = service.next_action("r1", "test", actor="agent")
    service.submit_check(
        "r1", "test", action_id=action["action_id"],
        state_version=action["state_version"], verdict="FAIL", verifier="reviewer",
        actor="agent", evidence={"cause": "implementation"},
    )
    with pytest.raises(AdaptiveControlError) as raised:
        service.submit_route(
            "r1", "test", target_task="implementation", reason="bad code",
            evidence="test proves it", still_valid=["requirements"],
            invalidated=["implementation"], actor="agent",
        )
    assert raised.value.code == "E_INVALID_CLOSURE"

    decision = service.submit_route(
        "r1", "test", target_task="implementation", reason="bad code",
        evidence="test proves it", still_valid=["requirements"],
        invalidated=["implementation", "test"], actor="agent",
    )
    assert decision["target_task"] == "implementation"
    assert decision["previous_run_id"] != decision["new_run_id"]
    assert mock_store._store["workflows/r1/tasks/implementation/validity"] == "INVALIDATED"
    assert mock_store._store["workflows/r1/tasks/requirements/validity"] == "VALID"


def test_only_visited_ancestors_are_recovery_targets(mock_store):
    service = _workflow(mock_store)
    mock_store.kv_delete("workflows/r1/tasks/requirements/status")
    assert service.allowed_recovery_targets("r1", "test") == ["test", "implementation"]


def test_uncompensated_side_effect_is_not_a_recovery_target(mock_store):
    service = _workflow(mock_store)
    dag = json.loads(mock_store._store["workflows/r1/dependencies"])
    dag["implementation"]["side_effecting"] = True
    mock_store.kv_put("workflows/r1/dependencies", json.dumps(dag))
    assert service.allowed_recovery_targets("r1", "test") == ["test", "requirements"]


def test_action_is_fenced_by_task_attempt_owner(mock_store):
    service = _workflow(mock_store)
    mock_store.kv_put("workflows/r1/tasks/test/attempt_id", "owned-attempt")
    with pytest.raises(AdaptiveControlError) as raised:
        service.next_action("r1", "test", actor="agent", attempt_id="stale")
    assert raised.value.code == "E_STALE_ATTEMPT"


def test_feedback_lifecycle_blocks_actions_until_applied(mock_store):
    service = _workflow(mock_store)
    item = service.deliver_feedback(
        "r1", "test", message="check the contract", actor="human",
    )
    next_action = service.next_action("r1", "test", actor="agent")
    assert next_action["type"] == "INTERPRET_FEEDBACK"
    assert next_action["feedback"]["status"] == "OBSERVED"
    service.respond_feedback(
        "r1", "test", feedback_id=item["feedback_id"], decision="continue",
        understanding="review the contract", reason="clear request", impact={},
        actor="agent",
    )
    action = service.next_action("r1", "test", actor="agent")
    assert action["action_type"] == "EXECUTE"


def test_ask_and_answer_use_waiting_for_human_state(mock_store):
    service = _workflow(mock_store)
    item = service.deliver_feedback("r1", "test", message="maybe incompatible", actor="human")
    service.next_action("r1", "test", actor="agent")
    service.respond_feedback(
        "r1", "test", feedback_id=item["feedback_id"], decision="ASK",
        understanding="compatibility is ambiguous", reason="business choice required",
        impact={"tasks": ["test"]}, actor="agent",
        question={"text": "Keep compatibility?", "options": ["yes", "no"]},
    )
    assert mock_store._store["workflows/r1/tasks/test/status"] == "WAITING_FOR_HUMAN"
    assert service.boundary("r1", "test")["kind"] == "AWAIT_HUMAN"
    result = service.answer_question("r1", "test", answer="yes", actor="human")
    assert result["question"]["status"] == "ANSWERED"
    assert mock_store._store["workflows/r1/tasks/test/status"] == "IN_PROGRESS"
    assert service.next_action("r1", "test", actor="agent")["type"] == "INTERPRET_FEEDBACK"


def test_hard_control_has_priority_over_feedback_and_can_resume(mock_store):
    service = _workflow(mock_store)
    service.deliver_feedback("r1", "test", message="note", actor="human")
    service.apply_control("r1", task="test", action="PAUSE", actor="human", reason="stop")
    assert service.boundary("r1", "test")["kind"] == "PAUSE"
    service.apply_control("r1", task="test", action="RESUME", actor="human", reason="continue")
    assert service.boundary("r1", "test")["kind"] == "FEEDBACK"
    service.apply_control("r1", action="ABORT", actor="human", reason="cancel")
    assert service.boundary("r1", "test")["kind"] == "ABORT"


def test_routing_budget_escalates_repeated_failure_to_human(mock_store):
    service = _workflow(mock_store)
    mock_store.kv_put("workflows/r1/routing/budget", json.dumps({
        "policy": {"max_total_routes": 1, "max_same_edge_routes": 1,
                   "max_same_failure_fingerprint": 1},
        "state": {"total": 1, "edges": {"test->test": 1},
                  "fingerprints": {"same": 1}},
    }))
    action = service.next_action("r1", "test", actor="agent")
    service.submit_check(
        "r1", "test", action_id=action["action_id"],
        state_version=action["state_version"], verdict="FAIL", verifier="reviewer",
        actor="agent", evidence={"cause": "same"},
    )
    with pytest.raises(AdaptiveControlError) as raised:
        service.submit_route(
            "r1", "test", target_task="test", reason="retry", evidence="same",
            still_valid=["requirements", "implementation"], invalidated=["test"],
            actor="agent", failure_fingerprint="same",
        )
    assert raised.value.code == "E_ROUTING_BUDGET_EXHAUSTED"
    assert service.boundary("r1", "test")["kind"] == "AWAIT_HUMAN"


def test_routing_budget_validation():
    with pytest.raises(ValueError):
        RoutingBudget.from_dict({"max_total_routes": 0})


def test_protocol_uses_real_local_store_cas():
    store = LocalStore()
    service = _workflow(store)
    action = service.next_action("r1", "test", actor="agent", attempt_id="a1")
    check = service.submit_check(
        "r1", "test", action_id=action["action_id"],
        state_version=action["state_version"], verdict="PASS", verifier="agent",
        actor="agent", evidence={"review": "passed"},
    )
    assert check["verdict"] == "PASS"
    route = service.next_action("r1", "test", actor="agent")
    assert route["type"] == "ROUTE"
    assert "__complete__" in route["allowed_targets"]
    completed = service.submit_route(
        "r1", "test", target_task="__complete__", reason="all checks passed",
        evidence="fresh verifier evidence", still_valid=[
            "requirements", "implementation", "test",
        ], invalidated=[], actor="agent",
    )
    assert completed["target_task"] == "__complete__"
    assert store.kv_get("workflows/r1/tasks/test/status")[0] == "DONE"


def test_completion_route_enforces_completion_contract(mock_store):
    service = _workflow(mock_store)
    mock_store.kv_put("workflows/r1/tasks/test/completion_contract", json.dumps({
        "required_artifacts": ["report"], "required_gates": ["review"],
    }))
    action = service.next_action("r1", "test", actor="agent")
    service.submit_check(
        "r1", "test", action_id=action["action_id"],
        state_version=action["state_version"], verdict="PASS", verifier="agent",
        actor="agent", evidence={"ok": True},
    )
    with pytest.raises(AdaptiveControlError) as raised:
        service.submit_route(
            "r1", "test", target_task="__complete__", reason="done", evidence="pass",
            still_valid=["test"], invalidated=[], actor="agent",
        )
    assert raised.value.code == "E_COMPLETION_CONTRACT"
