from __future__ import annotations

import pytest

from harness_framework.contracts import EvaluatorLoopPolicy
from harness_framework.evaluator import decide_evaluator_action


def _policy(**overrides) -> EvaluatorLoopPolicy:
    value = {
        "max_iterations": 3,
        "plateau_window": 2,
        "plateau_delta": 0.1,
        "fallback_chain": ["primary", "narrowed"],
        "escalation_target": "human",
    }
    value.update(overrides)
    return EvaluatorLoopPolicy.from_dict(value)


def test_pass_terminates_loop_immediately():
    decision = decide_evaluator_action(
        _policy(), strategy="primary", scores=[0.2], verdict="PASS"
    )
    assert decision.action == "PASS"
    assert decision.iteration == 1


def test_failed_iteration_retries_while_improvement_is_possible():
    decision = decide_evaluator_action(
        _policy(), strategy="primary", scores=[0.2], verdict="FAIL"
    )
    assert decision.action == "RETRY"


def test_score_plateau_switches_to_next_fallback():
    decision = decide_evaluator_action(
        _policy(), strategy="primary", scores=[0.50, 0.55], verdict="FAIL"
    )
    assert decision.action == "SWITCH_FALLBACK"
    assert decision.next_strategy == "narrowed"
    assert decision.reason == "score_plateau"


def test_max_iterations_switches_fallback_without_plateau():
    decision = decide_evaluator_action(
        _policy(plateau_window=4),
        strategy="primary", scores=[0.1, 0.4, 0.8], verdict="ERROR",
    )
    assert decision.action == "SWITCH_FALLBACK"
    assert decision.reason == "max_iterations_exceeded"


def test_final_fallback_exhaustion_escalates():
    decision = decide_evaluator_action(
        _policy(plateau_window=4),
        strategy="narrowed", scores=[0.1, 0.4, 0.8], verdict="FAIL",
    )
    assert decision.action == "ESCALATE"
    assert decision.next_strategy == ""


def test_unknown_strategy_is_rejected():
    with pytest.raises(ValueError, match="fallback_chain"):
        decide_evaluator_action(
            _policy(), strategy="unknown", scores=[0.2], verdict="FAIL"
        )
