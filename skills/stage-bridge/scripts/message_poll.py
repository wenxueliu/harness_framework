#!/usr/bin/env python3
"""
message_poll.py — 轮询当前任务的消息队列

用法：
  message_poll.py <req_id> [--status PENDING|DONE|...] [--limit N]

示例：
  message_poll.py req-001
  message_poll.py req-001 --status PENDING
  message_poll.py req-001 --limit 5
"""
import argparse
import base64
import json
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import env, kv_get, die

import os


def main():
    parser = argparse.ArgumentParser(description="轮询任务消息队列")
    parser.add_argument("req_id", help="需求 ID (如 req-001)")
    parser.add_argument("--task", dest="task_name", default="", help="任务名称（默认从 TASK_NAME 环境变量读取）")
    parser.add_argument("--status", default="", help="按状态过滤 (PENDING|PROCESSING|DONE|FAILED|TIMEOUT)")
    parser.add_argument("--limit", type=int, default=10, help="最多返回消息数，默认 10")
    parser.add_argument("--blocking", action="store_true", help="阻塞模式，等待新消息")
    parser.add_argument("--wait", default="30s", help="阻塞等待超时 (如 30s, 1m)")

    args = parser.parse_args()

    req_id = args.req_id
    task_name = args.task_name or env("TASK_NAME", "")

    if not task_name:
        die("任务名称未指定：设置 TASK_NAME 环境变量或使用 --task 参数", code=1)

    prefix = f"workflows/{req_id}/requests/{task_name}/"
    items, _ = kv_get(prefix, recurse=True)

    messages = []
    if items:
        for item in items:
            try:
                data = json.loads(item.get("_decoded", "{}"))
                if args.status and data.get("status") != args.status:
                    continue
                messages.append(data)
            except (json.JSONDecodeError, KeyError):
                continue

    messages.sort(key=lambda m: m.get("created_at", ""))
    messages = messages[:args.limit]

    emit_json({
        "req_id": req_id,
        "task": task_name,
        "count": len(messages),
        "messages": messages,
    })


def emit_json(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
