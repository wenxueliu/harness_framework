#!/usr/bin/env python3
"""
write_artifact.py — 写入任务产物到 Consul KV

用法：
  write_artifact.py <req_id> <key> <value>
  write_artifact.py <req_id> <key> --from-file path/to/value.json
  write_artifact.py <req_id> --scope context <key> <value>   # 写到需求上下文
  write_artifact.py <req_id> --scope task <key> <value>      # 写到当前任务（需 TASK_NAME）

scope 默认 task，写入路径 workflows/<req_id>/tasks/<task_name>/<key>
context 写入路径 workflows/<req_id>/context/<key>
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import (  # noqa: E402
    env, kv_put, task_base, context_base, emit_json, die
)


def main():
    p = argparse.ArgumentParser(description="写入产物")
    p.add_argument("req_id")
    p.add_argument("key")
    p.add_argument("value", nargs="?", default=None)
    p.add_argument("--from-file", default="",
                   help="从文件读取 value 内容（支持 JSON / 文本）")
    p.add_argument("--scope", choices=("task", "context"), default="task")
    args = p.parse_args()

    if args.from_file:
        with open(args.from_file, "r", encoding="utf-8") as f:
            value = f.read()
    elif args.value is not None:
        value = args.value
    else:
        die("必须提供 value 参数或 --from-file", code=1)

    if args.scope == "context":
        path = f"{context_base(args.req_id)}/{args.key}"
    else:
        task_name = env("TASK_NAME", required=True)
        path = f"{task_base(args.req_id, task_name)}/{args.key}"

    kv_put(path, value)
    emit_json({"ok": True, "path": path, "scope": args.scope, "size": len(value)})


if __name__ == "__main__":
    main()
