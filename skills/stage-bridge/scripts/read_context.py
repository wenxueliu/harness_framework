#!/usr/bin/env python3
"""
read_context.py — 读取需求级上下文

用法：
  read_context.py <req_id>              # 读取安全共享命名空间
  read_context.py <req_id> <key>        # 读取指定 namespaced key
  read_context.py <req_id> --wait <key> # 阻塞等待 key 出现（最长 5 分钟）
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import kv_get, context_base, emit_json, die, env  # noqa: E402


SAFE_NAMESPACES = ("facts", "artifacts", "summaries")


def main():
    p = argparse.ArgumentParser(description="读取需求上下文")
    p.add_argument("req_id")
    p.add_argument("key", nargs="?", default="",
                   help="可选：指定 key，如 api_spec_url")
    p.add_argument("--wait", action="store_true",
                   help="key 未出现时阻塞等待")
    p.add_argument("--timeout", type=int, default=300,
                   help="等待超时秒数（默认 300）")
    p.add_argument("--namespace", choices=SAFE_NAMESPACES,
                   help="只读取一个安全共享命名空间")
    args = p.parse_args()

    knowledge_base = f"workflows/{args.req_id}/knowledge"

    if args.key:
        if args.key.startswith("restricted/") or args.key.startswith("events/"):
            die("该上下文命名空间不能通过通用读取命令访问", code=1)
        if args.key.startswith("working_memory/"):
            task_name = env("TASK_NAME", required=True)
            allowed_prefix = f"working_memory/{task_name}/"
            if not args.key.startswith(allowed_prefix):
                die("不能读取其他任务的 working memory", code=1)
        namespaced = args.key.split("/", 1)[0] in {
            *SAFE_NAMESPACES, "working_memory",
        }
        target = f"{knowledge_base}/{args.key}" if namespaced else f"{context_base(args.req_id)}/{args.key}"
        deadline = time.time() + args.timeout
        while True:
            v, _ = kv_get(target)
            if v is not None:
                emit_json({"ok": True, "key": args.key, "value": v})
                return
            if not args.wait or time.time() >= deadline:
                if args.wait:
                    die(f"等待 {args.key} 超时（{args.timeout}s）", code=1)
                die(f"上下文 key {args.key} 不存在", code=1)
            time.sleep(3)

    result = {}
    namespaces = (args.namespace,) if args.namespace else SAFE_NAMESPACES
    for namespace in namespaces:
        base = f"{knowledge_base}/{namespace}"
        items, _ = kv_get(base, recurse=True)
        prefix = base + "/"
        if not items:
            continue
        for it in items:
            k = it["Key"].split(prefix, 1)[-1] if prefix in it["Key"] else it["Key"]
            result[f"{namespace}/{k}"] = it.get("_decoded", "")
    emit_json({"ok": True, "req_id": args.req_id, "context": result})


if __name__ == "__main__":
    main()
