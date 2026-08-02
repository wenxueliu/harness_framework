"""Deterministic evaluator-loop decisions with bounded fallback and escalation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from .contracts import EvaluatorLoopPolicy


@dataclass(frozen=True)
class EvaluationDecision:
    action: str
    strategy: str
    iteration: int
    reason: str
    next_strategy: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def decide_evaluator_action(
    policy: EvaluatorLoopPolicy,
    *,
    strategy: str,
    scores: Iterable[float],
    verdict: str,
) -> EvaluationDecision:
    """Return PASS, RETRY, SWITCH_FALLBACK, or ESCALATE for one observation."""
    if strategy not in policy.fallback_chain:
        raise ValueError(f"strategy is not in fallback_chain: {strategy}")
    if verdict not in {"PASS", "FAIL", "ERROR"}:
        raise ValueError("verdict must be PASS, FAIL, or ERROR")
    score_values = list(scores)
    if not score_values:
        raise ValueError("scores must contain the current observation")
    if any(isinstance(score, bool) or not isinstance(score, (int, float))
           for score in score_values):
        raise ValueError("scores must be numeric")

    iteration = len(score_values)
    if verdict == "PASS":
        return EvaluationDecision("PASS", strategy, iteration, "evaluator_passed")

    window = score_values[-policy.plateau_window:]
    plateau = (
        len(window) == policy.plateau_window
        and max(window) - min(window) <= policy.plateau_delta
    )
    exhausted = iteration >= policy.max_iterations
    if plateau or exhausted:
        reason = "score_plateau" if plateau else "max_iterations_exceeded"
        current_index = policy.fallback_chain.index(strategy)
        if current_index + 1 < len(policy.fallback_chain):
            next_strategy = policy.fallback_chain[current_index + 1]
            return EvaluationDecision(
                "SWITCH_FALLBACK", strategy, iteration, reason, next_strategy
            )
        return EvaluationDecision("ESCALATE", strategy, iteration, reason)

    return EvaluationDecision("RETRY", strategy, iteration, "improvement_still_possible")
