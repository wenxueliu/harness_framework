#!/usr/bin/env python3
"""Renew the renewable soft lease for the current task attempt."""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import emit_json, env, renew_attempt_lease  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="续租当前任务 attempt")
    parser.add_argument("req_id")
    parser.add_argument("task_name")
    parser.add_argument("--attempt-id", default=os.environ.get("ATTEMPT_ID", ""))
    parser.add_argument("--lease-epoch", default=os.environ.get("LEASE_EPOCH", ""))
    parser.add_argument(
        "--duration", type=int,
        default=int(env("LEASE_DURATION_SECONDS", "120")),
        help="软 lease 有效期（秒），默认 120",
    )
    args = parser.parse_args()

    ok, reason, expires_at = renew_attempt_lease(
        args.req_id, args.task_name, args.attempt_id, args.lease_epoch,
        args.duration, env("AGENT_ID", ""),
    )
    if not ok:
        emit_json({"ok": False, "error": reason})
        raise SystemExit(1)
    emit_json({"ok": True, "lease_expires_at": expires_at})


if __name__ == "__main__":
    main()
