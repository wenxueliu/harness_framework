#!/usr/bin/env python3
"""
pipeline.py — 设计管道: 设计文档 → dependencies.json → Consul

端到端打通设计仓到 Consul 的链路。

用法:
  # 从设计文档生成并同步
  python pipeline.py --design design.md --req-id REQ-001 --title "用户认证"

  # 从已有 deps.json 同步
  python pipeline.py --deps deps.json --req-id REQ-001 --title "用户认证"

  # 发布模式（Aggregator 立即调度）
  python pipeline.py --design design.md --req-id REQ-001 --title "用户认证" --publish

  # 预览模式（只生成 deps，不同步）
  python pipeline.py --design design.md --req-id REQ-001 --title "用户认证" --dry-run
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError


def find_script(name: str) -> str:
    """查找 skill 脚本路径。"""
    skill_dir = Path(__file__).resolve().parent.parent  # scripts/ → design-pipeline/
    candidates = [
        skill_dir / "scripts" / name,
        skill_dir.parent / "harness-sync" / "scripts" / name,
        Path.cwd() / "skills" / "harness-sync" / "scripts" / name,
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return name  # 希望它在 PATH 中


class ConsulClient:
    """基于 urllib 的 Consul HTTP 客户端（零外部依赖）。"""

    def __init__(self, addr: str = "127.0.0.1:8500"):
        self.base = f"http://{addr}/v1/kv"

    def _put(self, key: str, value: str) -> bool:
        url = f"{self.base}/{key}"
        req = Request(url, data=value.encode("utf-8"), method="PUT")
        try:
            resp = urlopen(req, timeout=5)
            return resp.status in (200, 204)
        except URLError as e:
            print(f"  Consul PUT 失败: {e}", file=sys.stderr)
            return False

    def _get(self, key: str) -> str | None:
        url = f"{self.base}/{key}"
        try:
            resp = urlopen(url, timeout=5)
            if resp.status == 200:
                data = json.loads(resp.read())
                if isinstance(data, list) and data:
                    val = data[0].get("Value", "")
                    if val:
                        import base64
                        return base64.b64decode(val).decode("utf-8")
                elif isinstance(data, dict):
                    return data.get("Value", "")
        except URLError:
            pass
        return None

    def health_check(self) -> bool:
        try:
            url = self.base.replace("/v1/kv", "/v1/status/leader")
            resp = urlopen(url, timeout=3)
            return resp.status == 200
        except URLError:
            return False


def now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sync_to_consul(req_id: str, deps: dict, title: str,
                   consul_addr: str, publish: bool) -> bool:
    """
    将 deps 同步到 Consul KV。

    直接使用 urllib（零外部依赖），不依赖 sync_to_consul.py。
    """
    consul = ConsulClient(consul_addr)

    # 健康检查
    if not consul.health_check():
        print("Error: Consul 不可达，请确认 Consul 已启动", file=sys.stderr)
        return False

    base = f"workflows/{req_id}"

    # 检查是否已存在
    existing = consul._get(f"{base}/title")
    if existing:
        print(f"警告: {req_id} 已存在 (title={existing})")
        print("  如需覆盖，请手动删除 Consul KV 或使用新的 req_id")
        return False

    ts = now_iso()

    # 写入元数据
    ok = all([
        consul._put(f"{base}/title", title),
        consul._put(f"{base}/dependencies", json.dumps(deps, ensure_ascii=False)),
        consul._put(f"{base}/created_at", ts),
    ])
    if not ok:
        print("Error: 写入元数据失败", file=sys.stderr)
        return False

    # 写入每个任务
    task_count = 0
    for task_name, info in deps.items():
        t_base = f"{base}/tasks/{task_name}"
        upstream = info.get("depends_on", [])
        initial_status = "PENDING" if not upstream else "BLOCKED"

        ok = all([
            consul._put(f"{t_base}/status", initial_status),
            consul._put(f"{t_base}/type", info.get("type", "backend")),
        ])
        if not ok:
            print(f"Error: 写入任务 {task_name} 失败", file=sys.stderr)
            continue

        if info.get("service_name"):
            consul._put(f"{t_base}/service_name", info["service_name"])
        if info.get("description"):
            consul._put(f"{t_base}/description", info["description"])
        if info.get("capability"):
            consul._put(f"{t_base}/capability", info["capability"])
        if upstream:
            # 展平 depends_on（可能是 dict 格式）
            dep_strs = []
            for d in upstream:
                if isinstance(d, dict):
                    dep_strs.append(d.get("task", ""))
                else:
                    dep_strs.append(d)
            consul._put(f"{t_base}/depends_on", ",".join(dep_strs))
        if info.get("blocking") is False:
            consul._put(f"{t_base}/blocking", "false")

        # metadata
        if info.get("metadata"):
            consul._put(f"{t_base}/metadata",
                        json.dumps(info["metadata"], ensure_ascii=False))
        if info.get("priority"):
            consul._put(f"{t_base}/priority", str(info["priority"]))

        consul._put(f"{t_base}/created_at", ts)
        task_count += 1

    # 发布
    published_val = "true" if publish else "false"
    consul._put(f"{base}/published", published_val)

    print(f"已同步: req_id={req_id}, {task_count} 个任务, published={published_val}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="设计管道: 设计文档 → dependencies.json → Consul"
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--design", help="设计文档路径 (.md)")
    source_group.add_argument("--deps", help="已有 dependencies.json 路径")

    parser.add_argument("--req-id", required=True, help="需求唯一 ID (如 REQ-20260502-001)")
    parser.add_argument("--title", required=True, help="需求标题")
    parser.add_argument("--output", default=None, help="deps.json 输出路径 (默认: _bmad-output/<req_id>/dependencies.json)")
    parser.add_argument("--consul", default=os.environ.get("CONSUL_ADDR", "127.0.0.1:8500"),
                        help="Consul 地址")
    parser.add_argument("--publish", action="store_true", help="直接发布（Aggregator 开始调度）")
    parser.add_argument("--wave", action="store_true", help="自动检测并包装 parallel wave")
    parser.add_argument("--dry-run", action="store_true", help="只生成 deps，不同步到 Consul")

    args = parser.parse_args()

    # ── 步骤 1: 生成 dependencies.json ──
    deps = None

    if args.deps:
        deps = json.loads(Path(args.deps).read_text(encoding="utf-8"))
        print(f"从 {args.deps} 读取 ({len(deps)} 个任务)")
    elif args.design:
        convert_script = find_script("design_to_deps.py")
        if not Path(args.design).exists():
            print(f"Error: 设计文档不存在: {args.design}", file=sys.stderr)
            sys.exit(1)

        # 确定输出路径
        if args.output:
            output_path = args.output
        else:
            output_dir = Path.cwd() / "_bmad-output" / args.req_id
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(output_dir / "dependencies.json")

        # 运行 converter
        cmd = [sys.executable, convert_script, args.design, "-o", output_path]
        if args.wave:
            cmd.append("--wave")

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            sys.exit(result.returncode)
        print(result.stdout.strip())

        deps = json.loads(Path(output_path).read_text(encoding="utf-8"))

    if not deps:
        print("Error: 未能生成 dependencies.json", file=sys.stderr)
        sys.exit(1)

    # ── 步骤 2: Dry run? ──
    if args.dry_run:
        print(f"\n[Dry Run] 未同步到 Consul")
        print(f"  req_id: {args.req_id}")
        print(f"  title: {args.title}")
        print(f"  任务数: {len(deps)}")
        print(f"  published: {args.publish}")
        print(f"\n任务列表:")
        for name, info in deps.items():
            deps_on = info.get("depends_on", [])
            dep_str = f" (依赖: {', '.join(deps_on)})" if deps_on else ""
            print(f"  [{info.get('type', '?')}] {name} → {info.get('service_name', '?')}{dep_str}")
        return

    # ── 步骤 3: 同步到 Consul ──
    print(f"\n同步到 Consul ({args.consul})...")
    success = sync_to_consul(args.req_id, deps, args.title, args.consul, args.publish)

    if success:
        print(f"\n管道完成!")
        print(f"  Consul UI: http://{args.consul}/ui/dc1/kv/workflows/{args.req_id}/")
        if args.publish:
            print(f"  状态: 已发布，Aggregator 开始调度")
        else:
            print(f"  状态: 草稿模式。发布: curl -X PUT http://{args.consul}/v1/kv/workflows/{args.req_id}/published -d true")
    else:
        print("\n管道失败，请检查 Consul 状态", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
