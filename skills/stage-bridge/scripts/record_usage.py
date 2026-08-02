#!/usr/bin/env python3
"""Add task resource usage and trip configured circuit breakers."""
from __future__ import annotations

import argparse
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

from _consul import emit_json, kv_delete, kv_get, kv_put  # noqa: E402
from harness_framework.budgets import BudgetLedger  # noqa: E402


class _KVAdapter:
    kv_get = staticmethod(kv_get)
    kv_put = staticmethod(kv_put)
    kv_delete = staticmethod(kv_delete)


def main() -> None:
    parser = argparse.ArgumentParser(description="记录任务资源用量")
    parser.add_argument("req_id")
    parser.add_argument("task_name")
    parser.add_argument("--tokens", type=int, default=0)
    parser.add_argument("--cost-usd", type=float, default=0)
    parser.add_argument("--tool-calls", type=int, default=0)
    parser.add_argument("--wall-clock-seconds", type=float, default=0)
    parser.add_argument("--attempt-id", default=os.environ.get("ATTEMPT_ID", ""))
    parser.add_argument("--lease-epoch", type=int, default=int(os.environ.get("LEASE_EPOCH", "0")))
    args = parser.parse_args()
    try:
        result = BudgetLedger(_KVAdapter()).consume(
            args.req_id, args.task_name, attempt_id=args.attempt_id,
            lease_epoch=args.lease_epoch, tokens=args.tokens,
            cost_usd=args.cost_usd, tool_calls=args.tool_calls,
            wall_clock_seconds=args.wall_clock_seconds,
        )
    except (ValueError, PermissionError, RuntimeError) as exc:
        emit_json({"ok": False, "error": str(exc)})
        raise SystemExit(1)
    emit_json({"ok": result["status"] == "ALLOW", **result})
    if result["status"] == "TRIPPED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
