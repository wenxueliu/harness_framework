#!/usr/bin/env python3
"""Record structured verifier evidence for a task completion gate."""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import (  # noqa: E402
    emit_json, env, kv_put, now_iso, task_base, validate_attempt,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="记录 verifier evidence")
    parser.add_argument("req_id")
    parser.add_argument("task_name")
    parser.add_argument("gate")
    parser.add_argument("verdict", choices=("PASS", "FAIL", "ERROR"))
    parser.add_argument("--details", default="{}")
    parser.add_argument("--artifact-ref", action="append", default=[])
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
    evidence = {
        "gate": args.gate, "verdict": args.verdict,
        "verifier": env("AGENT_ID", required=True), "observed_at": now_iso(),
        "details": details, "artifact_refs": args.artifact_ref,
        "producer_attempt_id": args.attempt_id,
        "producer_lease_epoch": int(args.lease_epoch),
    }
    base = f"{task_base(args.req_id, args.task_name)}/evidence/{args.gate}"
    kv_put(f"{base}/verdict", args.verdict)
    kv_put(f"{base}/record", json.dumps(evidence, ensure_ascii=False))
    emit_json({"ok": True, "evidence": evidence})


if __name__ == "__main__":
    main()
