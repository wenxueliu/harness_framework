#!/usr/bin/env python3
"""
feedback_write.py — 测试 Agent 失败后，向具体服务 Agent 写入修复反馈

用法：
  feedback_write.py <req_id> <service_name> --error "..." \
    [--failed-cases-file cases.json] [--log-url ...] [--har-url ...]

写入路径：workflows/<req_id>/feedback/<service_name>/...
触发后，绑定该 service 的开发 Agent 通过 feedback_listen.py 接收。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import (  # noqa: E402
    env, kv_put, feedback_base, emit_json, die, now_iso
)


def main():
    p = argparse.ArgumentParser(description="写入失败反馈给服务 Agent")
    p.add_argument("req_id")
    p.add_argument("service_name")
    p.add_argument("--error", required=True, help="错误摘要")
    p.add_argument("--failed-cases-file", default="",
                   help="失败用例 JSON 文件路径")
    p.add_argument("--log-url", default="")
    p.add_argument("--har-url", default="")
    p.add_argument("--severity", choices=("low", "medium", "high"),
                   default="medium")
    args = p.parse_args()

    reporter = env("AGENT_ID", required=True)
    base = feedback_base(args.req_id, args.service_name)

    payload = {
        "reporter": reporter,
        "ts": now_iso(),
        "service": args.service_name,
        "error_summary": args.error,
        "severity": args.severity,
        "log_url": args.log_url,
        "har_url": args.har_url,
    }

    if args.failed_cases_file:
        try:
            with open(args.failed_cases_file, "r", encoding="utf-8") as f:
                payload["failed_cases"] = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            die(f"读取失败用例文件出错: {e}", code=1)

    kv_put(f"{base}/payload", json.dumps(payload, ensure_ascii=False))
    kv_put(f"{base}/status", "PENDING_FIX")
    kv_put(f"{base}/last_updated", now_iso())

    emit_json({
        "ok": True,
        "service": args.service_name,
        "status": "PENDING_FIX",
        "path": base,
    })


if __name__ == "__main__":
    main()
