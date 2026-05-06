#!/usr/bin/env python3
"""
record_session_start.py — Agent 启动执行 Session

用法:
  record_session_start.py <req_id> <task_name> <session_id>

环境变量:
  AGENT_ID        全局唯一 Agent ID（必填）
  CONSUL_ADDR     Consul 地址（默认 127.0.0.1:8500）

KV 路径:
  workflows/<req_id>/runs/<run_id>/sessions/<task_name>/  ← 索引元数据
  workflows/<req_id>/sessions/<task_name>/<session_id>/   ← 事件流
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import (  # noqa: E402
    env, emit_json, die, now_iso,
    ensure_run, record_session_start, session_base, kv_put,
)


def main():
    p = argparse.ArgumentParser(description="启动 Agent 执行 Session")
    p.add_argument("req_id")
    p.add_argument("task_name")
    p.add_argument("session_id")
    args = p.parse_args()

    agent_id = env("AGENT_ID", required=True)

    # 获取或创建当前 run
    run_id = ensure_run(args.req_id)

    # 写入 session 索引元数据
    record_session_start(
        args.req_id, run_id, args.task_name,
        args.session_id, agent_id,
    )

    # 同步写入第一条事件日志
    ts = now_iso()
    seq = f"{int(__import__('time').time() * 1000000)}"
    payload = {
        "ts": ts,
        "agent_id": agent_id,
        "level": "info",
        "message": f"Session 启动: {args.task_name}",
        "step_type": "SESSION_START",
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
    })


if __name__ == "__main__":
    main()
