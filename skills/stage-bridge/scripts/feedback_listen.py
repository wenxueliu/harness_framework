#!/usr/bin/env python3
"""
feedback_listen.py — 服务 Agent 监听测试反馈（阻塞式）

用法：
  feedback_listen.py <req_id> <service_name> [--timeout 600]

行为：
  1. 阻塞监听 feedback/<service>/status，直到出现 PENDING_FIX
  2. CAS 抢占：将状态置为 FIXING，并写入 fixer = AGENT_ID
  3. stdout 输出 payload JSON，由调用方（编码智能体）执行实际修复
  4. 修复完成后，调用方应再次调用 feedback_resolve.py 写入 FIXED
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import (  # noqa: E402
    env, kv_get, kv_put, kv_blocking_get, feedback_base,
    emit_json, die, now_iso
)


def main():
    p = argparse.ArgumentParser(description="监听并抢占测试反馈")
    p.add_argument("req_id")
    p.add_argument("service_name")
    p.add_argument("--timeout", type=int, default=600,
                   help="总等待秒数（默认 600）")
    args = p.parse_args()

    agent_id = env("AGENT_ID", required=True)
    base = feedback_base(args.req_id, args.service_name)

    deadline = time.time() + args.timeout
    index = 0

    while time.time() < deadline:
        # 阻塞查询，最长 30s 一轮
        status, new_index = kv_blocking_get(
            f"{base}/status", index=index, wait="30s"
        )
        index = new_index
        if status != "PENDING_FIX":
            continue

        # 尝试 CAS 抢占
        _, mod_idx = kv_get(f"{base}/status")
        ok = kv_put(f"{base}/status", "FIXING", cas=mod_idx)
        if not ok:
            continue  # 被另一个实例抢先

        kv_put(f"{base}/fixer", agent_id)
        kv_put(f"{base}/fix_started_at", now_iso())

        # 读取 payload 供调用方处理
        payload_str, _ = kv_get(f"{base}/payload")
        payload = json.loads(payload_str) if payload_str else {}

        emit_json({
            "ok": True,
            "service": args.service_name,
            "claimed_by": agent_id,
            "payload": payload,
            "next_step": "完成修复后调用 feedback_resolve.py 标记 FIXED",
        })
        return

    die(f"等待反馈超时（{args.timeout}s）", code=1)


if __name__ == "__main__":
    main()
