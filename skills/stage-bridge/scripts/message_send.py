#!/usr/bin/env python3
"""
message_send.py — 向目标任务发送请求消息

用法：
  message_send.py <req_id> <to_task> <action> [--params JSON] [--timeout SECONDS]

示例：
  message_send.py req-001 task-design provide_api --params '{"endpoint": "/api/user"}'
  message_send.py req-001 task-backend review_code
"""
import argparse
import json
import sys
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import env, kv_put, die

import os


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def main():
    parser = argparse.ArgumentParser(description="向目标任务发送请求消息")
    parser.add_argument("req_id", help="需求 ID (如 req-001)")
    parser.add_argument("to_task", help="目标任务名称")
    parser.add_argument("action", help="请求动作类型")
    parser.add_argument("--from", dest="from_task", default="", help="源任务名称（默认从 TASK_NAME 环境变量读取）")
    parser.add_argument("--params", default="{}", help="参数字典 (JSON 格式)")
    parser.add_argument("--timeout", type=int, default=300, help="超时时间（秒），默认 300")

    args = parser.parse_args()

    req_id = args.req_id
    to_task = args.to_task
    from_task = args.from_task or env("TASK_NAME", "")

    if not from_task:
        die("源任务未指定：设置 TASK_NAME 环境变量或使用 --from 参数", code=1)

    try:
        params = json.loads(args.params)
    except json.JSONDecodeError as e:
        die(f"参数解析失败: {e}", code=1)

    msg_id = f"msg-{uuid.uuid4().hex[:12]}"
    msg = {
        "msg_id": msg_id,
        "req_id": req_id,
        "from": from_task,
        "to": to_task,
        "action": args.action,
        "params": params,
        "status": "PENDING",
        "result": None,
        "created_at": _now_iso(),
        "timeout": args.timeout,
    }

    key = f"workflows/{req_id}/requests/{to_task}/{msg_id}"
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
