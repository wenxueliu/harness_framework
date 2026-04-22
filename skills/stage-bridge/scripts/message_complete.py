#!/usr/bin/env python3
"""
message_complete.py — 完成消息处理，写入结果

用法：
  message_complete.py <req_id> <msg_id> [--result JSON]

示例：
  message_complete.py req-001 msg-abc123 --result '{"endpoint": "/api/v1/user"}'
  message_complete.py req-001 msg-abc123 --result '{"done": true}'
"""
import argparse
import json
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import env, kv_get, kv_put, die

import os


def main():
    parser = argparse.ArgumentParser(description="完成消息处理")
    parser.add_argument("req_id", help="需求 ID (如 req-001)")
    parser.add_argument("msg_id", help="消息 ID")
    parser.add_argument("--task", dest="task_name", default="", help="任务名称（默认从 TASK_NAME 环境变量读取）")
    parser.add_argument("--result", default="{}", help="处理结果 (JSON 格式)")

    args = parser.parse_args()

    req_id = args.req_id
    msg_id = args.msg_id
    task_name = args.task_name or env("TASK_NAME", "")

    if not task_name:
        die("任务名称未指定：设置 TASK_NAME 环境变量或使用 --task 参数", code=1)

    try:
        result = json.loads(args.result)
    except json.JSONDecodeError as e:
        die(f"结果解析失败: {e}", code=1)

    key = f"workflows/{req_id}/requests/{task_name}/{msg_id}"
    data, idx = kv_get(key)

    if not data:
        die(f"消息不存在: {msg_id}", code=1)

    try:
        msg = json.loads(data)
    except json.JSONDecodeError:
        die(f"消息格式错误: {msg_id}", code=1)

    msg["status"] = "DONE"
    msg["result"] = result

    kv_put(key, json.dumps(msg, ensure_ascii=False))

    emit_json({
        "ok": True,
        "msg_id": msg_id,
        "message": msg,
    })


def emit_json(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
