#!/usr/bin/env python3
"""Persist an attempt-fenced checkpoint for resume on the next retry."""
from __future__ import annotations

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

from _consul import emit_json, kv_get, kv_put, now_iso, task_base, validate_attempt  # noqa: E402
from harness_framework.contracts import CheckpointManifest  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="写入可恢复 checkpoint")
    parser.add_argument("req_id")
    parser.add_argument("task_name")
    parser.add_argument("cursor")
    parser.add_argument("payload", nargs="?", default=None)
    parser.add_argument("--from-file", default="")
    parser.add_argument("--artifact-ref", action="append", default=[])
    parser.add_argument("--attempt-id", default=os.environ.get("ATTEMPT_ID", ""))
    parser.add_argument("--lease-epoch", default=os.environ.get("LEASE_EPOCH", ""))
    args = parser.parse_args()

    valid, reason = validate_attempt(
        args.req_id, args.task_name, args.attempt_id, args.lease_epoch
    )
    if not valid:
        emit_json({"ok": False, "error": reason})
        raise SystemExit(1)
    if args.from_file:
        with open(args.from_file, encoding="utf-8") as handle:
            payload = handle.read()
    elif args.payload is not None:
        payload = args.payload
    else:
        emit_json({"ok": False, "error": "payload or --from-file is required"})
        raise SystemExit(1)

    base = f"{task_base(args.req_id, args.task_name)}/checkpoints"
    current, current_index = kv_get(f"{base}/current_version")
    version = int(current or "0") + 1
    manifest = CheckpointManifest.create(
        checkpoint_version=version, payload=payload,
        attempt_id=args.attempt_id, lease_epoch=int(args.lease_epoch),
        created_at=now_iso(), cursor=args.cursor, artifact_refs=args.artifact_ref,
    ).to_dict()
    version_base = f"{base}/versions/{version}"
    kv_put(f"{version_base}/payload", payload)
    kv_put(f"{version_base}/manifest", json.dumps(manifest, ensure_ascii=False))
    pointer_cas = current_index if current is not None else 0
    if not kv_put(f"{base}/current_version", str(version), cas=pointer_cas):
        emit_json({"ok": False, "error": "concurrent checkpoint publication detected"})
        raise SystemExit(1)
    emit_json({"ok": True, "version": version, "manifest": manifest})


if __name__ == "__main__":
    main()
