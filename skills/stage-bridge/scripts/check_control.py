#!/usr/bin/env python3
"""
check_control.py — 检查当前需求的控制信号（ACTIVE / PAUSED / ABORTED）

用于 P0-1 反应循环防护机制：
- 在 LLM 调用前后、verify 循环每轮、feedback-listen 每次唤醒时必须调用
- 若返回 ABORTED，立即调用 fail_task 并退出

用法：
  check_control.py <req_id> [--task <task_name>]

退出码：
  0 信号为 ACTIVE 或 PAUSE，可继续执行
  7 信号为 ABORT，立即退出
  1 系统错误（Consul 不可达等）
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import (  # noqa: E402
    env, kv_get, emit_json, die
)


def main():
    p = argparse.ArgumentParser(description="检查控制信号")
    p.add_argument("req_id")
    p.add_argument("--task", help="可选：检查特定任务的 control 快照")
    args = p.parse_args()

    # 优先级：任务级 control > 需求级 control
    if args.task:
        task_ctl, _ = kv_get(f"workflows/{args.req_id}/tasks/{args.task}/control")
        if task_ctl == "ABORT":
            emit_json({"ok": True, "signal": "ABORTED", "scope": f"task:{args.task}"})
            die("任务已收到 ABORT 信号", code=7)
        signal = task_ctl or ""
    else:
        signal, _ = kv_get(f"workflows/{args.req_id}/control")
        signal = signal or ""

    if signal == "ABORT":
        emit_json({"ok": True, "signal": "ABORTED", "scope": f"req:{args.req_id}"})
        die("需求已收到 ABORT 信号", code=7)

    emit_json({
        "ok": True,
        "signal": signal if signal else "ACTIVE",
        "scope": f"task:{args.task}" if args.task else f"req:{args.req_id}"
    })


if __name__ == "__main__":
    main()
