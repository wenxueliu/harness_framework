#!/usr/bin/env python3
"""
sync_to_consul.py — 将 dependencies.json 写入 Consul KV，初始化 workflow

用法:
  sync_to_consul.py <dependencies.json> --req-id <req_id> [--title "标题"] [--publish] [--force]

示例:
  sync_to_consul.py deps.json --req-id req-001 --title "用户登录功能"
  sync_to_consul.py deps.json --req-id req-001 --publish  # 直接发布
  sync_to_consul.py deps.json --req-id req-001 --force     # 覆盖已有 workflow

依赖:
  - 使用 harness_framework.consul_client.ConsulClient（仅标准库，无外部依赖）
  - Consul 必须在 CONSUL_ADDR（默认 127.0.0.1:8500）可访问
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import uuid

# 将项目根目录加入 sys.path，以支持从任意目录执行
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from harness_framework.consul_client import ConsulClient


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def validate_dependencies(data: dict) -> list[str]:
    """验证 dependencies.json 格式，返回错误列表（空列表 = 合法）。"""
    errors = []

    if not isinstance(data, dict):
        return ["dependencies.json must be a JSON object"]

    if "req_id" not in data and "REQ_ID" not in os.environ:
        errors.append("missing 'req_id' field (and REQ_ID env not set)")

    tasks = data.get("tasks", [])
    if not isinstance(tasks, list) or len(tasks) == 0:
        errors.append("'tasks' must be a non-empty array")
        return errors

    task_names = set()
    for i, task in enumerate(tasks):
        name = task.get("name", "")
        if not name:
            errors.append(f"task[{i}]: missing 'name'")
            continue
        if name in task_names:
            errors.append(f"task[{i}]: duplicate name '{name}'")
        task_names.add(name)

        t = task.get("type", "task")
        if t not in ("task", "parallel", "aggregate",
                     "design", "review", "backend", "test", "deploy"):
            errors.append(f"task '{name}': invalid type '{t}'")

        if t == "parallel" and "children" not in task:
            errors.append(f"parallel node '{name}': missing 'children'")

    # 验证 depends_on 引用的任务存在
    for task in tasks:
        for dep in task.get("depends_on", []):
            dep_name = dep.get("task", dep) if isinstance(dep, dict) else dep
            if dep_name not in task_names:
                errors.append(
                    f"task '{task['name']}': depends_on '{dep_name}' not found in tasks"
                )

    return errors


def workflow_exists(consul: ConsulClient, req_id: str) -> bool:
    """检查 workflow 是否已存在于 Consul KV。"""
    deps, _ = consul.kv_get(f"workflows/{req_id}/dependencies")
    return deps is not None


def write_workflow(
    consul: ConsulClient,
    req_id: str,
    data: dict,
    title: str = "",
    publish: bool = False,
) -> dict:
    """将 dependencies.json 写入 Consul KV。

    返回: {"ok": True, "task_count": N} 或 {"ok": False, "error": "..."}
    """
    tasks = data.get("tasks", [])
    guardrails = data.get("guardrails", {})

    # 1. 写入 dependencies（完整 DAG）
    deps_dict = {}
    for task in tasks:
        name = task["name"]
        deps_dict[name] = {
            "type": task.get("type", "task"),
            "depends_on": task.get("depends_on", []),
        }
        if task.get("type") == "parallel":
            deps_dict[name]["children"] = task.get("children", [])
        if "blocking" in task:
            deps_dict[name]["blocking"] = task["blocking"]
        if "non_blocking_deps" in task:
            deps_dict[name]["non_blocking_deps"] = task["non_blocking_deps"]

    consul.kv_put(f"workflows/{req_id}/dependencies", json.dumps(deps_dict))

    # 2. 遍历所有任务，写入初始状态
    for task in tasks:
        name = task["name"]
        node_type = task.get("type", "task")
        upstream = task.get("depends_on", [])

        # 判断初始状态：叶子任务（无依赖）→ PENDING，否则 → BLOCKED
        if node_type in ("parallel", "aggregate"):
            initial_status = "BLOCKED"
        elif not upstream:
            initial_status = "PENDING"
        else:
            # 检查是否所有依赖都是 non-blocking
            all_non_blocking = not task.get("blocking", True)
            if all_non_blocking:
                initial_status = "PENDING"
            else:
                # 检查 per-dependency blocking
                blocking_deps = [
                    d for d in upstream
                    if isinstance(d, dict) and d.get("blocking", True)
                ] if any(isinstance(d, dict) for d in upstream) else []
                if not isinstance(upstream[0], dict):
                    # 所有都是字符串 = 所有都是 blocking
                    initial_status = "BLOCKED"
                elif not blocking_deps:
                    # 所有依赖都是 non-blocking
                    initial_status = "PENDING"
                else:
                    initial_status = "BLOCKED"

        t_base = f"workflows/{req_id}/tasks/{name}"
        consul.kv_put(f"{t_base}/status", initial_status)
        consul.kv_put(f"{t_base}/type", node_type)

        if task.get("service_name"):
            consul.kv_put(f"{t_base}/service_name", task["service_name"])
        if task.get("capability"):
            consul.kv_put(f"{t_base}/capability", task["capability"])
        if task.get("description"):
            consul.kv_put(f"{t_base}/description", task["description"])
        if task.get("blocking") is not None:
            consul.kv_put(f"{t_base}/blocking", str(task["blocking"]).lower())
        if task.get("metadata"):
            consul.kv_put(f"{t_base}/metadata", json.dumps(task["metadata"]))
        if upstream:
            # 序列化 depends_on（支持字符串数组或对象数组）
            dep_strs = []
            for d in upstream:
                if isinstance(d, dict):
                    dep_strs.append(d.get("task", ""))
                else:
                    dep_strs.append(d)
            consul.kv_put(f"{t_base}/depends_on", ",".join(dep_strs))

        consul.kv_put(f"{t_base}/created_at", _now_iso())

    # 3. 写入 workflow 元数据
    consul.kv_put(f"workflows/{req_id}/title", title or data.get("title", ""))
    consul.kv_put(f"workflows/{req_id}/published", "true" if publish else "false")
    consul.kv_put(f"workflows/{req_id}/status", "IN_PROGRESS" if publish else "CONFIRMED")
    consul.kv_put(f"workflows/{req_id}/created_at", _now_iso())

    if guardrails:
        consul.kv_put(f"workflows/{req_id}/guardrails", json.dumps(guardrails))

    return {"ok": True, "task_count": len(tasks)}


def main():
    parser = argparse.ArgumentParser(
        description="将 dependencies.json 写入 Consul KV，初始化 workflow",
    )
    parser.add_argument(
        "deps_file",
        help="Path to dependencies.json file",
    )
    parser.add_argument(
        "--req-id", "-r",
        default=os.environ.get("REQ_ID", ""),
        help="Workflow requirement ID (or set REQ_ID env)",
    )
    parser.add_argument(
        "--title", "-t",
        default="",
        help="需求标题 (optional)",
    )
    parser.add_argument(
        "--publish", "-p",
        action="store_true",
        help="直接发布（默认写入为草稿，需手动设置 published=true 后 Aggregator 才调度）",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="覆盖已存在的 workflow",
    )
    parser.add_argument(
        "--consul",
        default=os.environ.get("CONSUL_ADDR", "127.0.0.1:8500"),
        help="Consul address (default: 127.0.0.1:8500 or CONSUL_ADDR env)",
    )
    args = parser.parse_args()

    # 读取 dependencies.json
    try:
        with open(args.deps_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: file not found: {args.deps_file}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON in {args.deps_file}: {e}", file=sys.stderr)
        sys.exit(1)

    # 确定 req_id
    req_id = args.req_id or data.get("req_id", "")
    if not req_id:
        print("Error: req_id is required (use --req-id or set 'req_id' in JSON)", file=sys.stderr)
        sys.exit(1)

    # 验证格式
    errors = validate_dependencies(data)
    if errors:
        print("Validation errors:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    # 连接 Consul
    consul = ConsulClient(addr=args.consul)

    # 幂等检查
    if workflow_exists(consul, req_id) and not args.force:
        print(f"Workflow '{req_id}' already exists. Use --force to overwrite.")
        sys.exit(0)

    if args.force and workflow_exists(consul, req_id):
        print(f"Overwriting existing workflow '{req_id}'...")
        # 将当前活跃 run 标记为 SUPERSEDED
        current_run, _ = consul.kv_get(f"workflows/{req_id}/current_run")
        if current_run:
            ts = _now_iso()
            run_base = f"workflows/{req_id}/runs/{current_run}"
            consul.kv_put(f"{run_base}/status", "SUPERSEDED")
            consul.kv_put(f"{run_base}/finished_at", ts)
            consul.kv_put(f"{run_base}/summary", json.dumps({
                "total": 0, "done": 0, "failed": 0, "aborted": 0,
                "note": "workflow overwritten by --force sync",
            }))
            consul.kv_delete(f"workflows/{req_id}/current_run")
            print(f"  Marked run {current_run} as SUPERSEDED")

    # 写入
    result = write_workflow(consul, req_id, data, title=args.title, publish=args.publish)

    if result["ok"]:
        published_str = "published" if args.publish else "draft (published=false)"
        print(
            f"Synced workflow '{req_id}' to Consul: "
            f"{result['task_count']} tasks, {published_str}"
        )
    else:
        print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
