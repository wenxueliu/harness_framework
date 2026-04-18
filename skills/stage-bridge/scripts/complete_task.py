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
    env, kv_put, task_base, emit_json, die, now_iso
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
    args = p.parse_args()

    agent_id = env("AGENT_ID", required=True)
    base = task_base(args.req_id, args.task_name)

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
    kv_put(f"{base}/status", final_status)

    emit_json({
        "ok": True,
        "req_id": args.req_id,
        "task_name": args.task_name,
        "status": final_status,
        "meta_written": list(meta.keys()),
    })


if __name__ == "__main__":
    main()
