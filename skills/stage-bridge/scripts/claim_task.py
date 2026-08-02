#!/usr/bin/env python3
"""
claim_task.py — 通过 CAS 原子操作抢占任务

用法：
  claim_task.py <req_id> <task_name>

退出码：
  0 抢占成功，stdout 输出任务上下文 JSON
  1 抢占失败（任务非 PENDING 或被其他 Agent 抢先）
  2 系统错误
"""
import argparse
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import (  # noqa: E402
    env, kv_get, kv_put, task_base, context_base,
    emit_json, die, now_iso, lease_deadline_iso,
    ensure_run, record_transition,
)


def main():
    p = argparse.ArgumentParser(description="CAS 抢占任务")
    p.add_argument("req_id")
    p.add_argument("task_name")
    args = p.parse_args()

    agent_id = env("AGENT_ID", required=True)
    base = task_base(args.req_id, args.task_name)

    workflow_status, _ = kv_get(f"workflows/{args.req_id}/status")
    if workflow_status == "Proposal":
        die("workflow 正在等待 Proposal 审批，任务抢占已冻结", code=1)

    control, _ = kv_get(f"workflows/{args.req_id}/control")
    if control in ("PAUSE", "ABORT"):
        die(f"workflow control={control}，任务抢占已冻结", code=1)

    # 1. 读取当前状态
    status, modify_index = kv_get(f"{base}/status")
    if status is None:
        die(f"任务 {args.req_id}/{args.task_name} 不存在", code=1)
    if status != "PENDING":
        die(f"任务状态为 {status}，非 PENDING，无法抢占", code=1)

    # 2. 检查 hint（若有指定 Agent，先核对）
    hint, _ = kv_get(f"{base}/assigned_agent_hint")
    if hint and hint != agent_id:
        # 只有 30 秒宽限期内 hint Agent 优先
        hint_ts, _ = kv_get(f"{base}/hint_assigned_at")
        if hint_ts:
            import datetime
            try:
                t = datetime.datetime.fromisoformat(hint_ts.rstrip("Z"))
                age = (datetime.datetime.utcnow() - t).total_seconds()
                if age < 30:
                    die(f"任务被指定给 {hint}（{int(age)}s 前），等待其抢占", code=1)
            except Exception:
                pass

    # 3. CAS 抢占
    ok = kv_put(f"{base}/status", "IN_PROGRESS", cas=modify_index)
    if not ok:
        die("CAS 失败，其他 Agent 抢先一步", code=1)

    # 4. 写入抢占元数据和 fencing token
    previous_epoch, _ = kv_get(f"{base}/lease_epoch")
    lease_epoch = int(previous_epoch or "0") + 1
    attempt_id = f"attempt-{uuid.uuid4().hex}"
    kv_put(f"{base}/attempt_id", attempt_id)
    kv_put(f"{base}/lease_epoch", str(lease_epoch))
    kv_put(f"{base}/assigned_agent", agent_id)
    claimed_at = now_iso()
    lease_duration = int(env("LEASE_DURATION_SECONDS", "120"))
    hard_timeout = int(env("HARD_TASK_TIMEOUT_SECONDS", "7200"))
    hard_deadline_at = lease_deadline_iso(hard_timeout)
    kv_put(f"{base}/started_at", claimed_at)
    kv_put(f"{base}/lease_renewed_at", claimed_at)
    kv_put(f"{base}/lease_expires_at", lease_deadline_iso(lease_duration))
    kv_put(f"{base}/hard_deadline_at", hard_deadline_at)

    # 5. 记录状态转换到 run 审计日志
    run_id = ensure_run(args.req_id)
    record_transition(
        args.req_id, run_id, args.task_name,
        previous_state="PENDING",
        new_state="IN_PROGRESS",
        actor=agent_id,
        reason="claimed by agent",
    )

    # 6. 读取任务完整 meta 与上下文
    task_items, _ = kv_get(f"{base}", recurse=True)
    task_meta = {}
    if task_items:
        for it in task_items:
            suffix = it["Key"].split(f"{base}/", 1)[-1] if "/" in it["Key"] else ""
            if suffix:
                task_meta[suffix] = it.get("_decoded", "")

    context_items, _ = kv_get(context_base(args.req_id), recurse=True)
    context = {}
    if context_items:
        prefix = context_base(args.req_id) + "/"
        for it in context_items:
            k = it["Key"].split(prefix, 1)[-1] if prefix in it["Key"] else it["Key"]
            context[k] = it.get("_decoded", "")

    emit_json({
        "ok": True,
        "agent_id": agent_id,
        "req_id": args.req_id,
        "task_name": args.task_name,
        "attempt_id": attempt_id,
        "lease_epoch": lease_epoch,
        "lease_duration_seconds": lease_duration,
        "hard_deadline_at": hard_deadline_at,
        "task_meta": task_meta,
        "context": context,
        "hints": {
            "next_steps": [
                "执行业务逻辑",
                "调用 log_step.py 记录关键事件",
                "调用 write_artifact.py 写入产物",
                "长任务需在 lease_expires_at 前调用 renew_lease.py 续租",
                "成功时调用 complete_task.py，失败时调用 fail_task.py",
            ]
        }
    })


if __name__ == "__main__":
    main()
