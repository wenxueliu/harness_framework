#!/usr/bin/env python3
"""
read_context.py — 读取需求级上下文

用法：
  read_context.py <req_id>              # 读取所有上下文
  read_context.py <req_id> <key>        # 读取指定 key
  read_context.py <req_id> --wait <key> # 阻塞等待 key 出现（最长 5 分钟）
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import kv_get, context_base, emit_json, die  # noqa: E402


def main():
    p = argparse.ArgumentParser(description="读取需求上下文")
    p.add_argument("req_id")
    p.add_argument("key", nargs="?", default="",
                   help="可选：指定 key，如 api_spec_url")
    p.add_argument("--wait", action="store_true",
                   help="key 未出现时阻塞等待")
    p.add_argument("--timeout", type=int, default=300,
                   help="等待超时秒数（默认 300）")
    args = p.parse_args()

    base = context_base(args.req_id)

    if args.key:
        deadline = time.time() + args.timeout
        while True:
            v, _ = kv_get(f"{base}/{args.key}")
            if v is not None:
                emit_json({"ok": True, "key": args.key, "value": v})
                return
            if not args.wait or time.time() >= deadline:
                if args.wait:
                    die(f"等待 {args.key} 超时（{args.timeout}s）", code=1)
                die(f"上下文 key {args.key} 不存在", code=1)
            time.sleep(3)

    items, _ = kv_get(base, recurse=True)
    result = {}
    if items:
        prefix = base + "/"
        for it in items:
            k = it["Key"].split(prefix, 1)[-1] if prefix in it["Key"] else it["Key"]
            result[k] = it.get("_decoded", "")
    emit_json({"ok": True, "req_id": args.req_id, "context": result})


if __name__ == "__main__":
    main()
