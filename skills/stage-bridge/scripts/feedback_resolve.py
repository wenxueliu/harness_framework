#!/usr/bin/env python3
"""
feedback_resolve.py — 服务 Agent 完成修复后标记反馈为 FIXED

用法：
  feedback_resolve.py <req_id> <service_name> --summary "修复了 XX 接口的鉴权"
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import (  # noqa: E402
    env, kv_put, feedback_base, emit_json, now_iso
)


def main():
    p = argparse.ArgumentParser(description="标记反馈已修复")
    p.add_argument("req_id")
    p.add_argument("service_name")
    p.add_argument("--summary", required=True, help="修复摘要")
    p.add_argument("--commit", default="", help="修复提交的 Git Commit Hash")
    args = p.parse_args()

    agent_id = env("AGENT_ID", required=True)
    base = feedback_base(args.req_id, args.service_name)

    kv_put(f"{base}/fix_summary", args.summary)
    if args.commit:
        kv_put(f"{base}/fix_commit", args.commit)
    kv_put(f"{base}/fix_completed_at", now_iso())
    kv_put(f"{base}/fixer", agent_id)
    kv_put(f"{base}/status", "FIXED")

    emit_json({
        "ok": True,
        "service": args.service_name,
        "status": "FIXED",
        "summary": args.summary,
    })


if __name__ == "__main__":
    main()
