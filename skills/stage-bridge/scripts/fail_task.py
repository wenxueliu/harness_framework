#!/usr/bin/env python3
"""
fail_task.py — 标记任务失败

用法：
  fail_task.py <req_id> <task_name> --error "原因摘要"
  fail_task.py <req_id> <task_name> --error "..." --traceback-file ./trace.txt
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import (  # noqa: E402
    env, kv_get, kv_put, task_base, emit_json, die, now_iso,
    ensure_run, record_transition, record_session_end, validate_attempt,
)


def main():
    p = argparse.ArgumentParser(description="失败任务")
    p.add_argument("req_id")
    p.add_argument("task_name")
    p.add_argument("--error", required=True)
    p.add_argument("--traceback-file", default="")
    p.add_argument("--log-url", default="",
                   help="错误日志的可访问 URL")
    p.add_argument("--retry-hint", choices=("retry", "blocked", "manual"),
                   default="retry", help="给 Watchdog 的处理建议")
    p.add_argument("--session-id", default="",
                   help="当前 Session ID，提供则在任务失败前自动关闭 Session")
    p.add_argument("--attempt-id", default=os.environ.get("ATTEMPT_ID", ""))
    p.add_argument("--lease-epoch", default=os.environ.get("LEASE_EPOCH", ""))
    args = p.parse_args()

    agent_id = env("AGENT_ID", required=True)
    base = task_base(args.req_id, args.task_name)
    valid, reason = validate_attempt(
        args.req_id, args.task_name, args.attempt_id, args.lease_epoch
    )
    if not valid:
        die(reason, code=1)

    kv_put(f"{base}/error_message", args.error)
    kv_put(f"{base}/failed_at", now_iso())
    kv_put(f"{base}/failed_by", agent_id)
    kv_put(f"{base}/retry_hint", args.retry_hint)

    if args.traceback_file:
        try:
            with open(args.traceback_file, "r", encoding="utf-8") as f:
                kv_put(f"{base}/traceback", f.read())
        except FileNotFoundError:
            sys.stderr.write(f"[warn] traceback 文件不存在: {args.traceback_file}\n")

    if args.log_url:
        kv_put(f"{base}/error_log_url", args.log_url)

    run_id = ensure_run(args.req_id)

    # 关闭 Session（若提供 session-id）
    if args.session_id:
        record_session_end(
            args.req_id, run_id, args.task_name,
            status="error",
            summary=args.error[:200],
        )

    # 记录状态转换
    prev_status, status_idx = kv_get(f"{base}/status")
    if prev_status != "IN_PROGRESS":
        die(f"task status is {prev_status}, expected IN_PROGRESS", code=1)
    record_transition(
        args.req_id, run_id, args.task_name,
        previous_state=prev_status or "IN_PROGRESS",
        new_state="FAILED",
        actor=agent_id,
        reason=args.error,
    )

    if not kv_put(f"{base}/status", "FAILED", cas=status_idx):
        die("task status changed concurrently; failure write fenced", code=1)

    emit_json({
        "ok": True,
        "req_id": args.req_id,
        "task_name": args.task_name,
        "status": "FAILED",
        "retry_hint": args.retry_hint,
    })


if __name__ == "__main__":
    main()
