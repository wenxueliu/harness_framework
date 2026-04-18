#!/usr/bin/env python3
"""
register_agent.py — Agent 手动注册到 Consul

用法：
  register_agent.py --capabilities backend,migration --service user-service \
    --max-concurrent 1 [--repo-path /path/to/repo]

环境变量（必填）：
  AGENT_ID — 全局唯一 Agent ID

退出码：0 成功 / 2 系统错误
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import (  # noqa: E402
    env, service_register, kv_put, emit_json, die, now_iso
)


def main():
    p = argparse.ArgumentParser(description="向 Consul 注册当前 Agent")
    p.add_argument("--capabilities", required=True,
                   help="逗号分隔，如 backend,migration")
    p.add_argument("--service", default="",
                   help="绑定的微服务名称（开发 Agent 必填）")
    p.add_argument("--max-concurrent", type=int, default=1,
                   help="最大并发任务数")
    p.add_argument("--repo-path", default="",
                   help="代码仓库本地路径（开发 Agent 必填）")
    p.add_argument("--agent-version", default="1.0.0")
    p.add_argument("--env", dest="environment", default="local")
    args = p.parse_args()

    agent_id = env("AGENT_ID", required=True)
    capabilities = [c.strip() for c in args.capabilities.split(",") if c.strip()]
    service_name = args.service or env("SERVICE_NAME", "")
    repo_path = args.repo_path or env("REPO_PATH", "")

    tags = [f"capability={c}" for c in capabilities]
    tags.append(f"env={args.environment}")
    tags.append(f"version={args.agent_version}")
    if service_name:
        tags.append(f"service={service_name}")

    payload = {
        "ID": agent_id,
        "Name": "agent-worker",
        "Tags": tags,
        "Meta": {
            "agent_id": agent_id,
            "capabilities": ",".join(capabilities),
            "max_concurrent": str(args.max_concurrent),
            "current_load": "0",
            "service_name": service_name,
            "repo_path": repo_path,
            "registered_at": now_iso(),
        },
        "Check": {
            "CheckID": f"service:{agent_id}",
            "Name": f"TTL check for {agent_id}",
            "TTL": "30s",
            "DeregisterCriticalServiceAfter": "2m",
        },
    }

    service_register(payload)

    # 同步写入 KV，便于框架快速查询负载
    kv_put(f"agents/{agent_id}/load", "0")
    kv_put(f"agents/{agent_id}/registered_at", now_iso())
    if service_name:
        kv_put(f"agents/{agent_id}/service", service_name)

    emit_json({
        "ok": True,
        "agent_id": agent_id,
        "capabilities": capabilities,
        "service": service_name,
        "tags": tags,
        "next_step": "调用 heartbeat.py 维持 TTL（建议每 10 秒一次）",
    })


if __name__ == "__main__":
    main()
