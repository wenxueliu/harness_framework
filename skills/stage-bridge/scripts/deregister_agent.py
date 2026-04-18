#!/usr/bin/env python3
"""
deregister_agent.py — Agent 主动从 Consul 注销

用法：
  deregister_agent.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import (  # noqa: E402
    env, service_deregister, kv_delete, emit_json
)


def main():
    agent_id = env("AGENT_ID", required=True)
    try:
        service_deregister(agent_id)
    except SystemExit:
        raise
    except Exception as e:
        sys.stderr.write(f"[warn] 注销失败（可能已自动过期）: {e}\n")

    # 清理 KV 中的负载信息
    try:
        kv_delete(f"agents/{agent_id}", recurse=True)
    except Exception:
        pass

    emit_json({"ok": True, "agent_id": agent_id, "status": "deregistered"})


if __name__ == "__main__":
    main()
