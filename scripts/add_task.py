#!/usr/bin/env python3
"""
add_task.py — 向已有 workflow 增量添加单个任务

用法：
  add_task.py <req_id> <task_name> --description "任务描述" --type backend --depends-on design,review

示例：
  add_task.py req-001 api-gateway --description "实现 API 网关" --type backend --depends-on backend
  add_task.py req-001 e2e-test --description "端到端测试" --type test --depends-on deploy

约束（两个方向都要检查）：
  1. 新任务的 depends_on 不能指向 FAILED/ABORTED 的任务（那些任务永远不会完成）
  2. 已完成（DONE/FAILED/ABORTED）的现有任务不能依赖新任务（它们不会重新跑）
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

TERMINAL_STATUSES = {"DONE", "FAILED", "ABORTED"}
FAIL_STATUSES = {"FAILED", "ABORTED"}


class ConsulClient:
    def __init__(self, addr: str):
        self.base_url = f"http://{addr}/v1/kv"

    def kv_put(self, key: str, value: str) -> bool:
        url = f"{self.base_url}/{key}"
        resp = requests.put(url, data=value.encode("utf-8"))
        return resp.status_code in (200, 204)

    def kv_get(self, key: str) -> str | None:
        url = f"{self.base_url}/{key}?raw"
        resp = requests.get(url)
        if resp.status_code == 404:
            return None
        return resp.text if resp.status_code == 200 else None

    def kv_get_all_tasks_status(self, req_id: str) -> dict[str, str]:
        base = f"workflows/{req_id}/tasks"
        url = f"{self.base_url}/{base}?keys=true"
        resp = requests.get(url)
        if resp.status_code != 200:
            return {}
        keys = resp.json()
        result = {}
        for key in keys:
            if key.endswith("/status"):
                task_name = key.replace(f"{base}/", "").replace("/status", "")
                status = self.kv_get(key)
                if status:
                    result[task_name] = status
        return result

    def kv_get_all_tasks_deps(self, req_id: str) -> dict[str, list[str]]:
        base = f"workflows/{req_id}/tasks"
        url = f"{self.base_url}/{base}?keys=true"
        resp = requests.get(url)
        if resp.status_code != 200:
            return {}
        keys = resp.json()
        result = {}
        for key in keys:
            if key.endswith("/depends_on"):
                task_name = key.replace(f"{base}/", "").replace("/depends_on", "")
                raw = self.kv_get(key)
                if raw:
                    result[task_name] = [x.strip() for x in raw.split(",") if x.strip()]
        return result


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main():
    parser = argparse.ArgumentParser(description="Add a single task to an existing workflow")
    parser.add_argument("req_id", help="Workflow ID (e.g. req-001)")
    parser.add_argument("task_name", help="New task name")
    parser.add_argument("--description", default="", help="Task description (what to do)")
    parser.add_argument("--type", default="generic", help="Task type: design, review, backend, test, deploy, generic")
    parser.add_argument("--depends-on", default="", help="Comma-separated list of upstream task names")
    parser.add_argument("--service-name", default="", help="Associated service name")
    parser.add_argument(
        "--consul",
        default=__import__("os").environ.get("CONSUL_ADDR", "127.0.0.1:8500"),
        help="Consul address (default: 127.0.0.1:8500)",
    )
    args = parser.parse_args()

    consul = ConsulClient(addr=args.consul)
    base = f"workflows/{args.req_id}"
    t_base = f"{base}/tasks/{args.task_name}"

    existing_status = consul.kv_get(f"{t_base}/status")
    if existing_status is not None:
        print(f"Error: Task '{args.task_name}' already exists in workflow '{args.req_id}'", file=sys.stderr)
        sys.exit(1)

    upstream = [x.strip() for x in args.depends_on.split(",") if x.strip()]
    all_statuses = consul.kv_get_all_tasks_status(args.req_id)
    all_deps = consul.kv_get_all_tasks_deps(args.req_id)

    broken_deps = []
    for dep in upstream:
        dep_status = all_statuses.get(dep, "")
        if dep_status in FAIL_STATUSES:
            broken_deps.append(f"{dep} ({dep_status})")
    if broken_deps:
        print(
            f"Error: Cannot add task. The following dependencies will never complete: "
            + ", ".join(broken_deps),
            file=sys.stderr,
        )
        sys.exit(1)

    blocked_by_done = []
    for task_name, deps in all_deps.items():
        if args.task_name in deps and all_statuses.get(task_name, "") in TERMINAL_STATUSES:
            blocked_by_done.append(f"{task_name} ({all_statuses[task_name]})")
    if blocked_by_done:
        print(
            f"Error: Cannot add task. The following already-completed tasks depend on it: "
            + ", ".join(blocked_by_done),
            file=sys.stderr,
        )
        sys.exit(1)

    all_done = all(all_statuses.get(dep, "") in TERMINAL_STATUSES for dep in upstream)
    initial_status = "PENDING" if (not upstream or all_done) else "BLOCKED"

    consul.kv_put(f"{t_base}/status", initial_status)
    consul.kv_put(f"{t_base}/type", args.type)
    if args.description:
        consul.kv_put(f"{t_base}/description", args.description)
    if args.service_name:
        consul.kv_put(f"{t_base}/service_name", args.service_name)
    if upstream:
        consul.kv_put(f"{t_base}/depends_on", ",".join(upstream))
    consul.kv_put(f"{t_base}/created_at", _now_iso())

    print(f"Added task '{args.task_name}' to '{args.req_id}' with status={initial_status}")


if __name__ == "__main__":
    main()
