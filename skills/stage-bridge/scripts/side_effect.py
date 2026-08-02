#!/usr/bin/env python3
"""Guard side effects with idempotency keys and trigger compensation."""
from __future__ import annotations

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

from _consul import emit_json, kv_delete, kv_get, kv_put  # noqa: E402
from harness_framework.side_effects import (  # noqa: E402
    IdempotencyConflict, SideEffectLedger,
)


class _KVAdapter:
    kv_get = staticmethod(kv_get)
    kv_put = staticmethod(kv_put)
    kv_delete = staticmethod(kv_delete)


def main() -> None:
    parser = argparse.ArgumentParser(description="幂等副作用与补偿任务")
    parser.add_argument("action", choices=("begin", "complete", "fail", "compensated"))
    parser.add_argument("req_id")
    parser.add_argument("task_name")
    parser.add_argument("idempotency_key")
    parser.add_argument("--attempt-id", default=os.environ.get("ATTEMPT_ID", ""))
    parser.add_argument("--lease-epoch", type=int, default=int(os.environ.get("LEASE_EPOCH", "0")))
    parser.add_argument("--result", default="{}")
    parser.add_argument("--error", default="")
    parser.add_argument("--source-task", default="")
    args = parser.parse_args()
    ledger = SideEffectLedger(_KVAdapter())
    try:
        if args.action == "begin":
            output = ledger.begin(
                args.req_id, args.task_name, args.idempotency_key,
                attempt_id=args.attempt_id, lease_epoch=args.lease_epoch,
            )
        elif args.action == "complete":
            result = json.loads(args.result)
            if not isinstance(result, dict):
                raise ValueError("--result must be a JSON object")
            output = ledger.complete(
                args.req_id, args.task_name, args.idempotency_key,
                attempt_id=args.attempt_id, lease_epoch=args.lease_epoch,
                result=result,
            )
        elif args.action == "fail":
            if not args.error:
                raise ValueError("--error is required for fail")
            output = ledger.fail_and_compensate(
                args.req_id, args.task_name, args.idempotency_key,
                attempt_id=args.attempt_id, lease_epoch=args.lease_epoch,
                error=args.error,
            )
        else:
            if not args.source_task:
                raise ValueError("--source-task is required for compensated")
            output = ledger.mark_compensated(
                args.req_id, args.source_task, args.idempotency_key,
                compensation_attempt_id=args.attempt_id,
                compensation_lease_epoch=args.lease_epoch,
                compensation_task=args.task_name,
            )
    except (ValueError, KeyError, PermissionError, IdempotencyConflict) as exc:
        emit_json({"ok": False, "error": str(exc)})
        raise SystemExit(1)
    emit_json({"ok": True, "result": output})


if __name__ == "__main__":
    main()
