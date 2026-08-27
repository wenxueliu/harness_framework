#!/usr/bin/env python3
"""
sync_to_consul.py — 将 dependencies.json 写入 Consul KV，初始化 workflow

用法:
  sync_to_consul.py <dependencies.json> --req-id <req_id> [--title "标题"] [--publish] [--force]

示例:
  sync_to_consul.py deps.json --req-id req-001 --title "用户登录功能"
  sync_to_consul.py deps.json --req-id req-001 --publish  # 直接发布
  sync_to_consul.py deps.json --req-id req-001 --force     # 覆盖已有 workflow

格式:
  dependencies.json 使用平铺 dict 格式，每个 key 为任务名，value 为任务定义。
  非任务元数据（req_id、title、guardrails）为顶层非 dict 字段，自动跳过。

  {
    "req_id": "req-001",
    "design": {
      "type": "design",
      "depends_on": [],
      "agent_name": "design-agent",
      "service_name": "myservice",
      "description": "..."
    },
    "backend": {
      "type": "backend",
      "depends_on": ["design"],
      "agent_name": "backend-agent",
      "service_name": "myservice",
      "description": "..."
    }
  }

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
from harness_framework.contracts import (
    AgentContract, CompletionContract, EvaluatorLoopPolicy, ReviewPolicy,
)
from harness_framework.versioning import VersionedResourceStore
from harness_framework.budgets import ResourceBudget
from harness_framework.recovery import RecoveryPolicy, validate_recovery_target
from harness_framework.model_execution import validate_execution


# 非任务元数据 key，自动从任务提取中排除
_META_KEYS = {
    "req_id", "title", "guardrails", "requirement", "workflow_spec", "plan",
}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def validate_dependencies(data: dict) -> list[str]:
    """验证 dependencies.json 格式（平铺 dict），返回错误列表（空列表 = 合法）。"""
    errors = []

    if not isinstance(data, dict):
        return ["dependencies.json must be a JSON object"]

    # 提取任务: value 为 dict 且 key 不是元数据字段
    tasks = {k: v for k, v in data.items() if isinstance(v, dict) and k not in _META_KEYS}

    if not tasks:
        errors.append("no tasks found (must have at least one task entry)")
        return errors

    task_names = set(tasks.keys())

    for name, info in tasks.items():
        t = info.get("type", "task")
        if t not in ("task", "parallel", "aggregate",
                     "design", "review", "backend", "test", "deploy"):
            errors.append(f"task '{name}': invalid type '{t}'")

        if t == "parallel" and "children" not in info:
            errors.append(f"parallel node '{name}': missing 'children'")

        agent_name = info.get("agent_name")
        service_name = info.get("service_name")
        if agent_name is not None and (
                not isinstance(agent_name, str) or not agent_name.strip()):
            errors.append(f"task '{name}': agent_name must be a non-empty string")
        if service_name is not None and not isinstance(service_name, str):
            errors.append(f"task '{name}': service_name must be a string")
        if t not in ("parallel", "aggregate") and not agent_name:
            errors.append(f"task '{name}': missing 'agent_name'")
        try:
            AgentContract.from_dict(info.get("agent_contract"))
            completion = CompletionContract.from_dict(info.get("completion_contract"))
            if "review_policy" in info:
                ReviewPolicy.from_dict(info["review_policy"])
                if "review" not in completion.required_gates:
                    errors.append(
                        f"task '{name}': review_policy requires completion_contract "
                        "gate 'review'"
                    )
            if "evaluator_policy" in info:
                EvaluatorLoopPolicy.from_dict(info["evaluator_policy"])
            if "resource_budget" in info:
                ResourceBudget.from_dict(info["resource_budget"])
            if "recovery_policy" in info:
                RecoveryPolicy.from_dict(info["recovery_policy"])
            if "execution" in info:
                validate_execution(info["execution"])
        except ValueError as exc:
            errors.append(f"task '{name}': {exc}")
        context_inputs = info.get("context_inputs", [])
        if not isinstance(context_inputs, list) or not all(
            isinstance(item, str) and item.strip() for item in context_inputs
        ):
            errors.append(f"task '{name}': context_inputs must be a list of strings")
        side_effecting = info.get("side_effecting", False)
        if not isinstance(side_effecting, bool):
            errors.append(f"task '{name}': side_effecting must be boolean")
        if side_effecting:
            compensation = info.get("compensation_task", "")
            if not isinstance(info.get("idempotency_scope"), str) or not info.get("idempotency_scope", "").strip():
                errors.append(f"task '{name}': side-effecting task requires idempotency_scope")
            if compensation not in task_names:
                errors.append(f"task '{name}': compensation_task '{compensation}' not found")
            elif tasks[compensation].get("activation") != "compensation_only":
                errors.append(
                    f"task '{name}': compensation task '{compensation}' must use activation=compensation_only"
                )
        activation = info.get("activation", "normal")
        if activation not in {"normal", "compensation_only"}:
            errors.append(f"task '{name}': invalid activation '{activation}'")

    # 验证 depends_on 引用的任务存在
    for name, info in tasks.items():
        for dep in info.get("depends_on", []):
            dep_name = dep.get("task", dep) if isinstance(dep, dict) else dep
            if dep_name not in task_names:
                errors.append(
                    f"task '{name}': depends_on '{dep_name}' not found in tasks"
                )
        if "review_policy" in info:
            try:
                review_policy = ReviewPolicy.from_dict(info["review_policy"])
                for target in review_policy.allowed_recovery_targets:
                    validate_recovery_target(tasks, name, target)
                if review_policy.default_recovery_target:
                    validate_recovery_target(
                        tasks, name, review_policy.default_recovery_target,
                        review_policy.allowed_recovery_targets,
                    )
            except ValueError as exc:
                errors.append(f"task '{name}': {exc}")
        execution = info.get("execution")
        if isinstance(execution, dict):
            session = execution.get("session", {})
            if session.get("mode") == "continue":
                source_task = session.get("from_task", "")
                if source_task not in task_names:
                    errors.append(
                        f"task '{name}': execution session source task "
                        f"'{source_task}' not found in tasks"
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
    """将 dependencies.json（平铺 dict）写入 Consul KV。

    返回: {"ok": True, "task_count": N} 或 {"ok": False, "error": "..."}
    """
    # 提取任务: value 为 dict 且 key 不是元数据字段
    tasks = {k: v for k, v in data.items() if isinstance(v, dict) and k not in _META_KEYS}
    guardrails = data.get("guardrails", {})

    # 1. 写入 dependencies（完整 DAG，纯任务 dict）
    deps_dict = {}
    for name, info in tasks.items():
        deps_dict[name] = {
            "type": info.get("type", "task"),
            "depends_on": info.get("depends_on", []),
        }
        if info.get("agent_name"):
            deps_dict[name]["agent_name"] = info["agent_name"]
        if info.get("type") == "parallel":
            deps_dict[name]["children"] = info.get("children", [])
        if "blocking" in info:
            deps_dict[name]["blocking"] = info["blocking"]
        if "non_blocking_deps" in info:
            deps_dict[name]["non_blocking_deps"] = info["non_blocking_deps"]
        if info.get("activation"):
            deps_dict[name]["activation"] = info["activation"]
        if "execution" in info:
            deps_dict[name]["execution"] = info["execution"]

    consul.kv_put(f"workflows/{req_id}/dependencies", json.dumps(deps_dict))

    # 2. 遍历所有任务，写入初始状态
    for name, info in tasks.items():
        node_type = info.get("type", "task")
        upstream = info.get("depends_on", [])

        # 判断初始状态：叶子任务（无依赖）→ PENDING，否则 → BLOCKED
        if info.get("activation") == "compensation_only":
            initial_status = "BLOCKED"
        elif node_type in ("parallel", "aggregate"):
            initial_status = "BLOCKED"
        elif not upstream:
            initial_status = "PENDING"
        else:
            all_non_blocking = not info.get("blocking", True)
            if all_non_blocking:
                initial_status = "PENDING"
            else:
                blocking_deps = [
                    d for d in upstream
                    if isinstance(d, dict) and d.get("blocking", True)
                ] if any(isinstance(d, dict) for d in upstream) else []
                if not isinstance(upstream[0], dict):
                    initial_status = "BLOCKED"
                elif not blocking_deps:
                    initial_status = "PENDING"
                else:
                    initial_status = "BLOCKED"

        t_base = f"workflows/{req_id}/tasks/{name}"
        consul.kv_put(f"{t_base}/status", initial_status)
        consul.kv_put(f"{t_base}/validity", "UNKNOWN")
        consul.kv_put(f"{t_base}/type", node_type)

        if info.get("agent_name"):
            consul.kv_put(f"{t_base}/agent_name", info["agent_name"])

        if info.get("service_name"):
            consul.kv_put(f"{t_base}/service_name", info["service_name"])
        if info.get("capability"):
            consul.kv_put(f"{t_base}/capability", info["capability"])
        if info.get("description"):
            consul.kv_put(f"{t_base}/description", info["description"])
        if info.get("blocking") is not None:
            consul.kv_put(f"{t_base}/blocking", str(info["blocking"]).lower())
        if info.get("metadata"):
            consul.kv_put(f"{t_base}/metadata", json.dumps(info["metadata"]))
        if "execution" in info:
            consul.kv_put(
                f"{t_base}/execution",
                json.dumps(info["execution"], ensure_ascii=False),
            )
        if "agent_contract" in info:
            contract = AgentContract.from_dict(info["agent_contract"])
            consul.kv_put(
                f"{t_base}/agent_contract",
                json.dumps(contract.to_dict(), ensure_ascii=False),
            )
        if "completion_contract" in info:
            contract = CompletionContract.from_dict(info["completion_contract"])
            consul.kv_put(
                f"{t_base}/completion_contract",
                json.dumps(contract.to_dict(), ensure_ascii=False),
            )
        if "review_policy" in info:
            policy = ReviewPolicy.from_dict(info["review_policy"])
            consul.kv_put(
                f"{t_base}/review_policy",
                json.dumps(policy.to_dict(), ensure_ascii=False),
            )
        if "evaluator_policy" in info:
            policy = EvaluatorLoopPolicy.from_dict(info["evaluator_policy"])
            consul.kv_put(
                f"{t_base}/evaluator_policy",
                json.dumps(policy.to_dict(), ensure_ascii=False),
            )
        consul.kv_put(
            f"{t_base}/context_inputs",
            json.dumps(info.get("context_inputs", []), ensure_ascii=False),
        )
        if "resource_budget" in info:
            budget = ResourceBudget.from_dict(info["resource_budget"])
            consul.kv_put(
                f"{t_base}/resource_budget",
                json.dumps(budget.to_dict(), ensure_ascii=False),
            )
        if info.get("side_effecting"):
            consul.kv_put(f"{t_base}/side_effecting", "true")
            consul.kv_put(f"{t_base}/idempotency_scope", info["idempotency_scope"])
            consul.kv_put(f"{t_base}/compensation_task", info["compensation_task"])
        if info.get("activation"):
            consul.kv_put(f"{t_base}/activation", info["activation"])
        if "recovery_policy" in info:
            policy = RecoveryPolicy.from_dict(info["recovery_policy"])
            consul.kv_put(
                f"{t_base}/recovery_policy",
                json.dumps(policy.to_dict(), ensure_ascii=False),
            )
        if upstream:
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

    if guardrails and isinstance(guardrails, dict):
        consul.kv_put(f"workflows/{req_id}/guardrails", json.dumps(guardrails))

    # 4. Publish independently addressable immutable resource revisions.  The
    # legacy keys above remain as compatibility projections during migration.
    versions = VersionedResourceStore(consul)
    versions.publish(
        req_id, "requirement",
        data.get("requirement", {
            "req_id": req_id, "title": title or data.get("title", ""),
        }),
        actor="sync_to_consul",
    )
    versions.publish(
        req_id, "workflow_spec", data.get("workflow_spec", tasks),
        actor="sync_to_consul",
    )
    versions.publish(req_id, "dag", deps_dict, actor="sync_to_consul")
    versions.publish(
        req_id, "plan", data.get("plan", {"tasks": list(tasks)}),
        actor="sync_to_consul",
    )

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
