#!/usr/bin/env python3
"""
write_artifact.py — 写入任务产物到 Consul KV

用法：
  write_artifact.py <req_id> <key> <value>
  write_artifact.py <req_id> <key> --from-file path/to/value.json
  write_artifact.py <req_id> --scope context <key> <value>   # 写到需求上下文
  write_artifact.py <req_id> --scope task <key> <value>      # 写到当前任务（需 TASK_NAME）

scope 默认 task，写入路径 workflows/<req_id>/tasks/<task_name>/<key>
context 写入路径 workflows/<req_id>/context/<key>
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import (  # noqa: E402
    env, kv_get, kv_put, task_base, context_base, emit_json, die,
    validate_attempt, create_artifact_manifest,
)


def main():
    p = argparse.ArgumentParser(description="写入产物")
    p.add_argument("req_id")
    p.add_argument("key")
    p.add_argument("value", nargs="?", default=None)
    p.add_argument("--from-file", default="",
                   help="从文件读取 value 内容（支持 JSON / 文本）")
    p.add_argument("--scope", choices=("task", "context"), default="task")
    p.add_argument("--attempt-id", default=os.environ.get("ATTEMPT_ID", ""))
    p.add_argument("--lease-epoch", default=os.environ.get("LEASE_EPOCH", ""))
    p.add_argument("--lineage", action="append", default=[],
                   help="上游 artifact manifest 路径；可重复")
    p.add_argument("--validation-status",
                   choices=("UNVALIDATED", "VALID", "INVALID"),
                   default="UNVALIDATED")
    p.add_argument("--retention", default="{}",
                   help="JSON retention metadata")
    args = p.parse_args()

    if args.from_file:
        with open(args.from_file, "r", encoding="utf-8") as f:
            value = f.read()
    elif args.value is not None:
        value = args.value
    else:
        die("必须提供 value 参数或 --from-file", code=1)

    if args.scope == "context":
        path = f"{context_base(args.req_id)}/{args.key}"
    else:
        task_name = env("TASK_NAME", required=True)
        valid, reason = validate_attempt(
            args.req_id, task_name, args.attempt_id, args.lease_epoch
        )
        if not valid:
            die(reason, code=1)
        path = f"{task_base(args.req_id, task_name)}/{args.key}"

        try:
            retention = json.loads(args.retention)
            if not isinstance(retention, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            die("--retention 必须是 JSON 对象", code=1)

        artifacts_base = f"{task_base(args.req_id, task_name)}/artifacts/{args.key}"
        current, _ = kv_get(f"{artifacts_base}/current_version")
        version = int(current or "0") + 1
        manifest = create_artifact_manifest(
            version=version,
            key=args.key,
            value=value,
            attempt_id=args.attempt_id,
            lease_epoch=args.lease_epoch,
            lineage=args.lineage,
            validation_status=args.validation_status,
            retention=retention,
        )
        version_base = f"{artifacts_base}/versions/{version}"
        kv_put(f"{version_base}/value", value)
        kv_put(
            f"{version_base}/manifest",
            json.dumps(manifest, ensure_ascii=False),
        )
        kv_put(f"{artifacts_base}/current_version", str(version))

    kv_put(path, value)
    response = {"ok": True, "path": path, "scope": args.scope, "size": len(value)}
    if args.scope == "task":
        response.update({"artifact_version": version, "manifest": manifest})
    emit_json(response)


if __name__ == "__main__":
    main()
