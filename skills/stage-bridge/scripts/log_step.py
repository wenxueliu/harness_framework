#!/usr/bin/env python3
"""
log_step.py — 记录任务执行日志到 Consul 会话事件流

用法：
  log_step.py <req_id> <message> [--level info|warn|error] [--data '{"k":"v"}']

默认 session_id 从环境变量 SESSION_ID 读取，未设置则自动以 AGENT_ID + 时间戳生成。

KV 路径：workflows/<req_id>/sessions/<task_name>/<session_id>/events/<seq>
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import (  # noqa: E402
    env, kv_get, kv_put, session_base, emit_json, die, now_iso
)


def main():
    p = argparse.ArgumentParser(description="记录执行日志")
    p.add_argument("req_id")
    p.add_argument("message")
    p.add_argument("--level", default="info",
                   choices=("debug", "info", "warn", "error"))
    p.add_argument("--data", default="",
                   help="附加 JSON 数据，将被合并到事件 payload 中")
    p.add_argument("--task", default="",
                   help="覆盖 TASK_NAME 环境变量")
    args = p.parse_args()

    agent_id = env("AGENT_ID", required=True)
    task_name = args.task or env("TASK_NAME", required=True)
    session_id = env("SESSION_ID", "")
    if not session_id:
        # 自动生成（建议 Agent 启动时显式设置）
        session_id = f"{agent_id}-{int(time.time())}"

    payload = {
        "ts": now_iso(),
        "agent_id": agent_id,
        "level": args.level,
        "message": args.message,
    }
    if args.data:
        try:
            payload["data"] = json.loads(args.data)
        except json.JSONDecodeError as e:
            die(f"--data 不是合法 JSON: {e}", code=1)

    base = session_base(args.req_id, task_name, session_id)

    # 用单调递增 seq；本地 MVP 简化为时间戳微秒
    seq = f"{int(time.time() * 1000000)}"
    kv_put(f"{base}/events/{seq}", json.dumps(payload, ensure_ascii=False))

    # 同时写入 latest_event 便于看板快速读取
    kv_put(f"{base}/latest_event", json.dumps(payload, ensure_ascii=False))

    emit_json({
        "ok": True,
        "session_id": session_id,
        "seq": seq,
        "path": f"{base}/events/{seq}",
    })


if __name__ == "__main__":
    main()
