#!/usr/bin/env python3
"""Select and persist the next recovery path for the current failure."""
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

from _consul import emit_json, kv_get, kv_put, now_iso, task_base, validate_attempt  # noqa: E402
from harness_framework.recovery import RecoveryPolicy, select_recovery_path  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="选择失败恢复路径")
    parser.add_argument("req_id")
    parser.add_argument("task_name")
    parser.add_argument("--attempts-used", type=int)
    parser.add_argument("--attempt-id", default=os.environ.get("ATTEMPT_ID", ""))
    parser.add_argument("--lease-epoch", default=os.environ.get("LEASE_EPOCH", ""))
    args = parser.parse_args()
    valid, reason = validate_attempt(
        args.req_id, args.task_name, args.attempt_id, args.lease_epoch
    )
    if not valid:
        emit_json({"ok": False, "error": reason})
        raise SystemExit(1)
    base = task_base(args.req_id, args.task_name)
    failure_raw, _ = kv_get(f"{base}/failure/current")
    if not failure_raw:
        emit_json({"ok": False, "error": "task has no current failure envelope"})
        raise SystemExit(1)
    policy_raw, _ = kv_get(f"{base}/recovery_policy")
    retry_count, _ = kv_get(f"{base}/retry_count")
    try:
        failure = json.loads(failure_raw)
        policy = RecoveryPolicy.from_dict(json.loads(policy_raw) if policy_raw else None)
        attempts_used = args.attempts_used if args.attempts_used is not None else int(retry_count or 0)
        decision = select_recovery_path(policy, failure, attempts_used)
    except (json.JSONDecodeError, ValueError) as exc:
        emit_json({"ok": False, "error": str(exc)})
        raise SystemExit(1)
    record = {
        **decision.to_dict(), "attempts_used": attempts_used,
        "failure_id": failure.get("failure_id", ""), "selected_at": now_iso(),
    }
    seq = f"{int(time.time() * 1000000):021d}"
    kv_put(f"{base}/recovery/current", json.dumps(record, ensure_ascii=False))
    kv_put(f"{base}/recovery/history/{seq}", json.dumps(record, ensure_ascii=False))
    if decision.path == "HUMAN":
        escalation = {
            "status": "OPEN", "target": decision.escalation_target,
            "reason": decision.reason, "task_name": args.task_name,
            "failure_id": failure.get("failure_id", ""), "created_at": now_iso(),
        }
        kv_put(
            f"workflows/{args.req_id}/human_interventions/{args.task_name}/{seq}",
            json.dumps(escalation, ensure_ascii=False),
        )
    emit_json({"ok": True, "decision": record})


if __name__ == "__main__":
    main()
