#!/usr/bin/env python3
"""
complete_task.py — 标记任务完成并写入元数据

用法：
  complete_task.py <req_id> <task_name>
  complete_task.py <req_id> <task_name> --meta '{"branch":"feature/req-001","commit":"abc"}'
  complete_task.py <req_id> <task_name> --await-review --pr-url https://...
    （写入 AWAITING_REVIEW 状态，由 Webhook 后续转为 DONE）
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import (  # noqa: E402
    env, kv_get, kv_put, task_base, emit_json, die, now_iso,
    ensure_run, record_transition, record_session_end, validate_attempt,
    check_completion_contract,
)


def main():
    p = argparse.ArgumentParser(description="完成任务")
    p.add_argument("req_id")
    p.add_argument("task_name")
    p.add_argument("--meta", default="",
                   help="JSON 格式的元数据，将逐 key 写入 KV")
    p.add_argument("--await-review", action="store_true",
                   help="进入 AWAITING_REVIEW 状态而非 DONE")
    p.add_argument("--pr-url", default="",
                   help="配合 --await-review，记录 PR URL")
    p.add_argument("--session-id", default="",
                   help="当前 Session ID，提供则在任务完成前自动关闭 Session")
    p.add_argument("--attempt-id", default=os.environ.get("ATTEMPT_ID", ""))
    p.add_argument("--lease-epoch", default=os.environ.get("LEASE_EPOCH", ""))
    args = p.parse_args()

    agent_id = env("AGENT_ID", required=True)
    base = task_base(args.req_id, args.task_name)
    valid, reason = validate_attempt(
        args.req_id, args.task_name, args.attempt_id, args.lease_epoch
    )
    if not valid:
        die(reason, code=1)

    meta = {}
    if args.meta:
        try:
            meta = json.loads(args.meta)
            if not isinstance(meta, dict):
                die("--meta 必须是 JSON 对象", code=1)
        except json.JSONDecodeError as e:
            die(f"--meta 不是合法 JSON: {e}", code=1)

    if args.pr_url:
        meta["pr_url"] = args.pr_url

    # 写入元数据
    for k, v in meta.items():
        kv_put(f"{base}/{k}",
               v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))

    kv_put(f"{base}/last_updated", now_iso())
    kv_put(f"{base}/completed_by", agent_id)

    final_status = "AWAITING_REVIEW" if args.await_review else "DONE"
    if final_status == "DONE":
        ready, missing = check_completion_contract(args.req_id, args.task_name)
        if not ready:
            die("completion contract unsatisfied: " + ", ".join(missing), code=1)

    run_id = ensure_run(args.req_id)

    # 关闭 Session（若提供 session-id）
    if args.session_id:
        record_session_end(
            args.req_id, run_id, args.task_name,
            status="completed",
            summary=f"任务完成: {final_status}",
        )

    # 记录状态转换
    prev_status, status_idx = kv_get(f"{base}/status")
    if prev_status != "IN_PROGRESS":
        die(f"task status is {prev_status}, expected IN_PROGRESS", code=1)
    record_transition(
        args.req_id, run_id, args.task_name,
        previous_state=prev_status or "IN_PROGRESS",
        new_state=final_status,
        actor=agent_id,
        reason="task completed",
    )

    if not kv_put(f"{base}/status", final_status, cas=status_idx):
        die("task status changed concurrently; completion fenced", code=1)

    emit_json({
        "ok": True,
        "req_id": args.req_id,
        "task_name": args.task_name,
        "status": final_status,
        "meta_written": list(meta.keys()),
    })


if __name__ == "__main__":
    main()
