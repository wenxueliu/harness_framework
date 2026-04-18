#!/usr/bin/env python3
"""
heartbeat.py — 维持 Agent 的 Consul TTL Check 通过状态

用法：
  heartbeat.py                  # 单次心跳
  heartbeat.py --loop 10        # 后台循环，每 10 秒一次（前台运行，按 Ctrl+C 退出）

环境变量：AGENT_ID（必填）
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import env, health_check_pass, emit_json  # noqa: E402


def beat(agent_id: str) -> None:
    health_check_pass(f"service:{agent_id}", note="alive")


def main():
    p = argparse.ArgumentParser(description="Agent 心跳上报")
    p.add_argument("--loop", type=int, default=0,
                   help="循环间隔秒数；0 表示单次执行")
    args = p.parse_args()

    agent_id = env("AGENT_ID", required=True)

    if args.loop <= 0:
        beat(agent_id)
        emit_json({"ok": True, "agent_id": agent_id, "mode": "single"})
        return

    # 循环模式
    sys.stderr.write(f"[heartbeat] {agent_id} 启动循环心跳，间隔 {args.loop}s\n")
    while True:
        try:
            beat(agent_id)
        except SystemExit:
            raise
        except Exception as e:
            sys.stderr.write(f"[heartbeat] 失败: {e}\n")
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
