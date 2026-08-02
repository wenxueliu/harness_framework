#!/usr/bin/env python3
"""Record an evaluator observation and apply the configured loop policy."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

from _consul import (  # noqa: E402
    emit_json, env, kv_get, kv_put, now_iso, task_base, validate_attempt,
)
from harness_framework.contracts import EvaluatorLoopPolicy  # noqa: E402
from harness_framework.evaluator import decide_evaluator_action  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="记录 evaluator-loop 评分并执行策略")
    parser.add_argument("req_id")
    parser.add_argument("task_name")
    parser.add_argument("score", type=float)
    parser.add_argument("verdict", choices=("PASS", "FAIL", "ERROR"))
    parser.add_argument("--strategy", default="")
    parser.add_argument("--details", default="{}")
    parser.add_argument("--attempt-id", default=os.environ.get("ATTEMPT_ID", ""))
    parser.add_argument("--lease-epoch", default=os.environ.get("LEASE_EPOCH", ""))
    args = parser.parse_args()

    valid, reason = validate_attempt(
        args.req_id, args.task_name, args.attempt_id, args.lease_epoch
    )
    if not valid:
        emit_json({"ok": False, "error": reason})
        raise SystemExit(1)
    try:
        details = json.loads(args.details)
        if not isinstance(details, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        emit_json({"ok": False, "error": "--details must be a JSON object"})
        raise SystemExit(1)

    base = task_base(args.req_id, args.task_name)
    raw_policy, _ = kv_get(f"{base}/evaluator_policy")
    try:
        policy = EvaluatorLoopPolicy.from_dict(
            json.loads(raw_policy) if raw_policy else None
        )
    except (json.JSONDecodeError, ValueError) as exc:
        emit_json({"ok": False, "error": f"invalid evaluator_policy: {exc}"})
        raise SystemExit(1)

    state_raw, state_index = kv_get(f"{base}/evaluator/state")
    try:
        state = json.loads(state_raw) if state_raw else {}
    except json.JSONDecodeError:
        state = {}
    if state.get("status") in {"PASS", "ESCALATE"}:
        emit_json({"ok": False, "error": f"evaluator loop is terminal: {state['status']}"})
        raise SystemExit(1)
    configured_strategy = state.get("strategy") or policy.fallback_chain[0]
    if args.strategy and args.strategy != configured_strategy:
        emit_json({
            "ok": False,
            "error": f"strategy is fenced; expected {configured_strategy}",
        })
        raise SystemExit(1)
    strategy = configured_strategy
    prior_scores = state.get("scores", []) if state.get("strategy") == strategy else []
    scores = [*prior_scores, args.score]
    try:
        decision = decide_evaluator_action(
            policy, strategy=strategy, scores=scores, verdict=args.verdict
        )
    except ValueError as exc:
        emit_json({"ok": False, "error": str(exc)})
        raise SystemExit(1)

    observed_at = now_iso()
    seq = f"{int(time.time() * 1000000):021d}"
    observation = {
        "score": args.score,
        "verdict": args.verdict,
        "strategy": strategy,
        "iteration": len(scores),
        "observed_at": observed_at,
        "details": details,
        "producer_attempt_id": args.attempt_id,
        "producer_lease_epoch": int(args.lease_epoch),
        "decision": decision.to_dict(),
    }
    next_state = {
        "status": decision.action,
        "strategy": decision.next_strategy or strategy,
        "scores": [] if decision.action == "SWITCH_FALLBACK" else scores,
        "updated_at": observed_at,
        "reason": decision.reason,
    }
    if not kv_put(
        f"{base}/evaluator/state",
        json.dumps(next_state, ensure_ascii=False),
        cas=state_index,
    ):
        emit_json({"ok": False, "error": "concurrent evaluator update detected"})
        raise SystemExit(1)
    kv_put(f"{base}/evaluator/history/{seq}", json.dumps(observation, ensure_ascii=False))
    if decision.action == "ESCALATE":
        escalation = {
            "status": "OPEN",
            "target": policy.escalation_target,
            "reason": decision.reason,
            "task_name": args.task_name,
            "strategy": strategy,
            "attempt_id": args.attempt_id,
            "created_at": observed_at,
        }
        kv_put(f"{base}/evaluator/escalation", json.dumps(escalation, ensure_ascii=False))
        kv_put(
            f"workflows/{args.req_id}/human_interventions/{args.task_name}/{seq}",
            json.dumps(escalation, ensure_ascii=False),
        )

    emit_json({"ok": True, "observation": observation, "state": next_state})


if __name__ == "__main__":
    main()
