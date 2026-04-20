#!/usr/bin/env python3
"""
claim_next_task.py — 自动查找并抢占下一个可用任务

用法：
  # 抢占单个任务
  claim_next_task.py

  # 循环执行模式（抢占-执行-完成循环，直到没有可用任务）
  claim_next_task.py --loop

  # 指定 capabilities 过滤
  claim_next_task.py --capabilities backend,translate

  # 仅查看可用任务，不抢占
  claim_next_task.py --list-only

退出码：
  0 抢占成功，stdout 输出任务上下文 JSON
  1 无可用任务 / 抢占失败 / --list-only 模式
  2 系统错误
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import (  # noqa: E402
    env, kv_get, kv_put, emit_json, die, now_iso
)


def req_priority(req_id: str) -> int:
    """获取需求的优先级，默认 0"""
    pri, _ = kv_get(f"workflows/{req_id}/priority")
    return int(pri) if pri else 0


def find_pending_tasks() -> list[dict]:
    """查找所有 PENDING 状态的任务"""
    items, _ = kv_get("workflows", recurse=True)
    if not items:
        return []

    pending_tasks = []
    task_keys = {}

    # 收集所有 task status
    for it in items:
        key = it.get("Key", "")
        value = it.get("_decoded", "")
        if "/tasks/" in key and key.endswith("/status"):
            # workflows/<req_id>/tasks/<task_name>/status
            parts = key.split("/tasks/")
            if len(parts) == 2:
                req_id = parts[0].split("/")[-1]
                task_name = parts[1].replace("/status", "")
                task_keys[(req_id, task_name)] = value

    # 对每个 PENDING 任务，收集元数据
    for (req_id, task_name), status in task_keys.items():
        if status == "PENDING":
            task_meta, _ = kv_get(f"workflows/{req_id}/tasks/{task_name}", recurse=True)
            meta = {}
            if task_meta:
                for it in task_meta:
                    k = it.get("Key", "").split(f"workflows/{req_id}/tasks/{task_name}/")[-1]
                    meta[k] = it.get("_decoded", "")

            pending_tasks.append({
                "req_id": req_id,
                "req_priority": req_priority(req_id),
                "task_name": task_name,
                "status": status,
                "type": meta.get("type", "generic"),
                "service_name": meta.get("service_name", ""),
                "description": meta.get("description", ""),
                "assigned_agent_hint": meta.get("assigned_agent_hint", ""),
            })

    return pending_tasks


def claim_task(req_id: str, task_name: str, agent_id: str) -> tuple[bool, dict]:
    """
    尝试抢占指定任务。
    返回 (success, result_dict)
    """
    base = f"workflows/{req_id}/tasks/{task_name}"

    # 1. 读取当前状态
    status, modify_index = kv_get(f"{base}/status")
    if status is None:
        return False, {"error": f"任务 {req_id}/{task_name} 不存在"}

    if status != "PENDING":
        return False, {"error": f"任务状态为 {status}，非 PENDING"}

    # 2. 检查 hint（若有指定 Agent，先核对）
    hint, _ = kv_get(f"{base}/assigned_agent_hint")
    if hint and hint != agent_id:
        hint_ts, _ = kv_get(f"{base}/hint_assigned_at")
        if hint_ts:
            import datetime
            try:
                t = datetime.datetime.fromisoformat(hint_ts.rstrip("Z"))
                age = (datetime.datetime.utcnow() - t).total_seconds()
                if age < 30:
                    return False, {"error": f"任务被指定给 {hint}（{int(age)}s 前）"}
            except Exception:
                pass

    # 3. CAS 抢占
    ok = kv_put(f"{base}/status", "IN_PROGRESS", cas=modify_index)
    if not ok:
        return False, {"error": "CAS 失败，其他 Agent 抢先"}

    # 4. 写入抢占元数据
    kv_put(f"{base}/assigned_agent", agent_id)
    kv_put(f"{base}/started_at", now_iso())

    # 5. 读取任务完整 meta 与上下文
    task_items, _ = kv_get(f"{base}", recurse=True)
    task_meta = {}
    if task_items:
        for it in task_items:
            suffix = it["Key"].split(f"{base}/", 1)[-1] if "/" in it["Key"] else ""
            if suffix:
                task_meta[suffix] = it.get("_decoded", "")

    context_items, _ = kv_get(f"workflows/{req_id}/context", recurse=True)
    context = {}
    if context_items:
        prefix = f"workflows/{req_id}/context/"
        for it in context_items:
            k = it["Key"].split(prefix, 1)[-1] if prefix in it["Key"] else it["Key"]
            context[k] = it.get("_decoded", "")

    return True, {
        "ok": True,
        "agent_id": agent_id,
        "req_id": req_id,
        "task_name": task_name,
        "task_meta": task_meta,
        "context": context,
    }


def filter_and_rank_tasks(
    tasks: list[dict],
    agent_id: str,
    capabilities: list[str],
    service_name: str,
) -> list[dict]:
    """
    过滤并排序任务。
    优先规则：
    1. 优先匹配 service_name（如果已绑定服务）
    2. 按 depends_on 数量排序（依赖少的先执行）
    3. 按 type 匹配 capabilities
    """
    deps_json, _ = kv_get("workflows", recurse=True)
    deps_map = {}
    if deps_json:
        for it in deps_json:
            key = it.get("Key", "")
            if key.endswith("/dependencies"):
                try:
                    deps = json.loads(it.get("_decoded", "{}"))
                    for req_id in deps:
                        for task_name, info in deps.items():
                            deps_map[(req_id, task_name)] = info.get("depends_on", [])
                except json.JSONDecodeError:
                    pass

    import json

    ranked = []
    for task in tasks:
        req_id = task["req_id"]
        task_name = task["task_name"]
        task_service = task.get("service_name", "")
        task_type = task.get("type", "generic")

        score = 0

        # 需求优先级（权重最大）
        score += task.get("req_priority", 0) * 100

        # 匹配 service_name（高优先级）
        if service_name and task_service == service_name:
            score += 100

        # 匹配 capabilities
        type_capabilities = {
            "design": ["design"],
            "review": ["review"],
            "backend": ["backend"],
            "frontend": ["frontend"],
            "test": ["test"],
            "deploy": ["deploy"],
        }
        required_caps = type_capabilities.get(task_type, [])
        if any(cap in capabilities for cap in required_caps):
            score += 50

        # 依赖数量少优先
        deps = deps_map.get((req_id, task_name), [])
        score += max(0, 20 - len(deps))

        # 有 hint 且是自己
        if task.get("assigned_agent_hint") == agent_id:
            score += 30

        ranked.append((score, task))

    ranked.sort(key=lambda x: -x[0])  # 按分数降序
    return [t for _, t in ranked]


def main():
    p = argparse.ArgumentParser(description="自动查找并抢占下一个可用任务")
    p.add_argument(
        "--loop",
        action="store_true",
        help="循环模式：抢占-执行-完成循环，直到没有可用任务",
    )
    p.add_argument(
        "--capabilities",
        default="",
        help="逗号分隔的 capabilities，用于过滤任务类型",
    )
    p.add_argument(
        "--list-only",
        action="store_true",
        help="仅列出可用任务，不抢占",
    )
    p.add_argument(
        "--wait",
        type=int,
        default=0,
        help="无可用任务时等待秒数（loop 模式）",
    )
    p.add_argument(
        "--max-tasks",
        type=int,
        default=0,
        help="最多执行任务数（0=不限，loop 模式）",
    )
    args = p.parse_args()

    agent_id = env("AGENT_ID", required=True)
    service_name = env("SERVICE_NAME", "")
    capabilities = [c.strip() for c in args.capabilities.split(",") if c.strip()]
    if not capabilities:
        capabilities = [env("CAPABILITIES", "")]  # 兼容旧配置

    import json

    while True:
        # 查找所有 PENDING 任务
        pending = find_pending_tasks()

        if not pending:
            if args.list_only:
                emit_json({"ok": True, "tasks": [], "message": "当前无 PENDING 任务"})
                return

            if args.loop:
                if args.wait > 0:
                    print(f"[claim_next] 无可用任务，等待 {args.wait}s...", file=sys.stderr)
                    time.sleep(args.wait)
                    continue
                else:
                    print("[claim_next] 无可用任务，退出", file=sys.stderr)
                    break
            else:
                die("当前无 PENDING 任务", code=1)

        # 过滤并排序
        filtered = filter_and_rank_tasks(pending, agent_id, capabilities, service_name)

        if not filtered:
            if args.loop:
                if args.wait > 0:
                    time.sleep(args.wait)
                    continue
                else:
                    break
            die("无可匹配的任务（capabilities 或 service_name 不匹配）", code=1)

        # 尝试抢占第一个任务
        task = filtered[0]
        success, result = claim_task(task["req_id"], task["task_name"], agent_id)

        if success:
            result["_hint"] = "任务抢占成功。请执行业务逻辑，完成后再次调用 claim_next_task.py 抢占下一个任务。"
            if args.loop:
                result["_hint"] += " 或使用 --loop 模式自动循环。"
            emit_json(result)

            if args.loop:
                max_tasks = args.max_tasks
                if max_tasks > 0:
                    max_tasks -= 1
                    args.max_tasks = max_tasks
                if args.max_tasks == 0:
                    break
        else:
            if args.loop:
                # 任务可能被抢，短暂等待后重试
                time.sleep(1)
                continue
            else:
                die(result.get("error", "抢占失败"), code=1)

    # loop 模式正常退出
    if args.loop:
        emit_json({"ok": True, "message": "所有可用任务已处理完毕"})


if __name__ == "__main__":
    main()
