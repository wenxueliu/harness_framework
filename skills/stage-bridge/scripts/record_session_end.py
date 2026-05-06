#!/usr/bin/env python3
"""
record_session_end.py — Agent 关闭执行 Session

用法:
  record_session_end.py <req_id> <task_name> <session_id>
      [--status completed|error|aborted] [--summary "..."]
      [--event-count N] [--error-count N]

环境变量:
  AGENT_ID        全局唯一 Agent ID（必填）
  CONSUL_ADDR     Consul 地址（默认 127.0.0.1:8500）

若未指定 --event-count / --error-count，则自动扫描 events/ 统计。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import (  # noqa: E402
    env, emit_json, die, now_iso,
    ensure_run, record_session_end, session_base, kv_get, kv_put,
)


def _count_events(base: str) -> tuple[int, int]:
    """扫描 session 的 events/ 目录，返回 (event_count, error_count)。"""
    items, _ = kv_get(f"{base}/events/", recurse=True)
    if not items:
        return 0, 0

    total = 0
    errors = 0
    for it in items:
        try:
            data = json.loads(it.get("_decoded", "{}"))
            if isinstance(data, dict):
                total += 1
                if data.get("level") == "error":
                    errors += 1
        except json.JSONDecodeError:
            total += 1

    return total, errors


def main():
    p = argparse.ArgumentParser(description="关闭 Agent 执行 Session")
    p.add_argument("req_id")
    p.add_argument("task_name")
    p.add_argument("session_id")
    p.add_argument("--status", default="completed",
                   choices=("completed", "error", "aborted"))
    p.add_argument("--summary", default="")
    p.add_argument("--event-count", type=int, default=-1,
                   help="手动指定事件数（-1 则自动统计）")
    p.add_argument("--error-count", type=int, default=-1,
                   help="手动指定错误数（-1 则自动统计）")
    args = p.parse_args()

    agent_id = env("AGENT_ID", required=True)

    # 获取当前 run
    run_id = ensure_run(args.req_id)

    # 自动统计事件数
    event_count = args.event_count
    error_count = args.error_count
    if event_count < 0 or error_count < 0:
        base = session_base(args.req_id, args.task_name, args.session_id)
        auto_total, auto_errors = _count_events(base)
        if event_count < 0:
            event_count = auto_total
        if error_count < 0:
            error_count = auto_errors

    # 写入 session 索引结束元数据
    record_session_end(
        args.req_id, run_id, args.task_name,
        event_count=event_count,
        error_count=error_count,
        status=args.status,
        summary=args.summary,
    )

    # 写入关闭事件
    ts = now_iso()
    seq = f"{int(__import__('time').time() * 1000000)}"
    payload = {
        "ts": ts,
        "agent_id": agent_id,
        "level": "info" if args.status == "completed" else "error",
        "message": (f"Session 关闭: {args.task_name} ({args.status})"
                    + (f" — {args.summary}" if args.summary else "")),
        "step_type": "SESSION_END",
        "run_id": run_id,
    }
    base = session_base(args.req_id, args.task_name, args.session_id)
    kv_put(f"{base}/events/{seq}",
           json.dumps(payload, ensure_ascii=False))
    kv_put(f"{base}/latest_event",
           json.dumps(payload, ensure_ascii=False))

    emit_json({
        "ok": True,
        "session_id": args.session_id,
        "run_id": run_id,
        "task_name": args.task_name,
        "req_id": args.req_id,
        "status": args.status,
        "event_count": event_count,
        "error_count": error_count,
    })


if __name__ == "__main__":
    main()
