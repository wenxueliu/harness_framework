#!/usr/bin/env python3
"""
sync_to_consul.py — 将本地 dependencies.json 写入 Consul，初始化一个需求

用法：
  sync_to_consul.py <req_id> <dependencies.json> [--title "需求标题"]

示例 dependencies.json：
{
  "design-api": {
    "type": "design",
    "depends_on": [],
    "service_name": null,
    "description": "为新功能设计 API 契约"
  },
  "build-user-service": {
    "type": "backend",
    "depends_on": ["design-api"],
    "service_name": "user-service",
    "description": "实现 user-service 的认证逻辑"
  },
  "test-e2e": {
    "type": "test",
    "depends_on": ["build-user-service"],
    "service_name": null
  }
}
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from harness_framework.consul_client import ConsulClient  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("req_id")
    p.add_argument("deps_file")
    p.add_argument("--title", default="")
    p.add_argument("--consul", default=os.environ.get("CONSUL_ADDR", "127.0.0.1:8500"))
    args = p.parse_args()

    with open(args.deps_file, "r", encoding="utf-8") as f:
        deps = json.load(f)

    consul = ConsulClient(addr=args.consul)

    base = f"workflows/{args.req_id}"
    if args.title:
        consul.kv_put(f"{base}/title", args.title)
    consul.kv_put(f"{base}/dependencies", json.dumps(deps, ensure_ascii=False))
    consul.kv_put(f"{base}/created_at", _now_iso())

    # 为每个任务初始化基础元数据
    for task_name, info in deps.items():
        t_base = f"{base}/tasks/{task_name}"
        upstream = info.get("depends_on", [])
        # 叶子任务（无依赖）直接 PENDING；其余 BLOCKED
        initial_status = "PENDING" if not upstream else "BLOCKED"
        consul.kv_put(f"{t_base}/status", initial_status)
        consul.kv_put(f"{t_base}/type", info.get("type", "generic"))
        if info.get("service_name"):
            consul.kv_put(f"{t_base}/service_name", info["service_name"])
        if info.get("description"):
            consul.kv_put(f"{t_base}/description", info["description"])
        consul.kv_put(f"{t_base}/created_at", _now_iso())

    print(f"已同步 req_id={args.req_id}，{len(deps)} 个任务")


def _now_iso() -> str:
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"


if __name__ == "__main__":
    main()
