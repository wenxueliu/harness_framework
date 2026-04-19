#!/usr/bin/env python3
"""
sync_to_consul.py — 将本地 dependencies.json 写入 Consul，初始化一个需求

直接使用 requests 库调用 Consul HTTP API，无需外部依赖。

用法：
  sync_to_consul.py <req_id> <dependencies.json> [--title "需求标题"] [--consul HOST:PORT]

示例：
  sync_to_consul.py req-001 /tmp/deps.json --title "用户登录功能"
  sync_to_consul.py req-002 deps.json --consul 127.0.0.1:8500
"""
import argparse
import datetime
import json
import sys

try:
    import requests
except ImportError:
    print("Error: requests library is required. Install with: pip install requests", file=sys.stderr)
    raise SystemExit(1)


class ConsulClient:
    """直接封装 Consul HTTP API 的简单客户端"""

    def __init__(self, addr: str):
        self.base_url = f"http://{addr}/v1/kv"

    def kv_put(self, key: str, value: str) -> bool:
        """写入单个 KV，返回是否成功"""
        url = f"{self.base_url}/{key}"
        resp = requests.put(url, data=value.encode("utf-8"))
        return resp.status_code in (200, 204)

    def kv_get(self, key: str) -> str | None:
        """读取单个 KV 值，返回 None 如果不存在"""
        url = f"{self.base_url}/{key}"
        resp = requests.get(url)
        if resp.status_code == 404:
            return None
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                return data.get("Value")
            elif isinstance(data, list) and data:
                return data[0].get("Value")
        return None


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main():
    parser = argparse.ArgumentParser(
        description="Sync dependencies.json to Consul for Harness Framework"
    )
    parser.add_argument("req_id", help="需求唯一标识符 (如 req-001)")
    parser.add_argument("deps_file", help="dependencies.json 文件路径")
    parser.add_argument("--title", default="", help="需求标题")
    parser.add_argument(
        "--consul",
        default=__import__("os").environ.get("CONSUL_ADDR", "127.0.0.1:8500"),
        help="Consul 地址 (默认: 127.0.0.1:8500)",
    )
    args = parser.parse_args()

    # 加载依赖配置
    with open(args.deps_file, "r", encoding="utf-8") as f:
        deps = json.load(f)

    consul = ConsulClient(addr=args.consul)
    base = f"workflows/{args.req_id}"

    # 写入元数据
    if args.title:
        consul.kv_put(f"{base}/title", args.title)
    consul.kv_put(f"{base}/dependencies", json.dumps(deps, ensure_ascii=False))
    consul.kv_put(f"{base}/created_at", _now_iso())

    # 写入每个任务
    for task_name, info in deps.items():
        t_base = f"{base}/tasks/{task_name}"
        upstream = info.get("depends_on", [])
        initial_status = "PENDING" if not upstream else "BLOCKED"

        consul.kv_put(f"{t_base}/status", initial_status)
        consul.kv_put(f"{t_base}/type", info.get("type", "generic"))

        if info.get("service_name"):
            consul.kv_put(f"{t_base}/service_name", info["service_name"])
        if info.get("description"):
            consul.kv_put(f"{t_base}/description", info["description"])
        if upstream:
            consul.kv_put(f"{t_base}/depends_on", ",".join(upstream))

        consul.kv_put(f"{t_base}/created_at", _now_iso())

    print(f"已同步 req_id={args.req_id}，{len(deps)} 个任务")


if __name__ == "__main__":
    main()
