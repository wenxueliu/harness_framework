#!/usr/bin/env python3
"""Change a requirement in-place and rerun selected tasks plus downstream."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from harness_framework.consul_client import ConsulClient  # noqa: E402
from harness_framework.requirement_changes import (  # noqa: E402
    RequirementChangeService,
)


def parse_tasks(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Publish a new requirement revision in the same workflow and "
            "rerun selected tasks plus their downstream closure"
        )
    )
    parser.add_argument("req_id", help="Existing workflow requirement ID")
    parser.add_argument(
        "--content-file", required=True,
        help="UTF-8 file containing the complete new requirement text",
    )
    parser.add_argument("--reason", required=True, help="Reason for the change")
    parser.add_argument(
        "--redo", "--changed-tasks", dest="changed_tasks", default="",
        help="Comma-separated tasks changed by the requirement; downstream is automatic",
    )
    parser.add_argument(
        "--actor", default=os.environ.get("AGENT_ID", ""),
        help="Person or agent applying the change (or set AGENT_ID)",
    )
    parser.add_argument(
        "--consul", default=os.environ.get("CONSUL_ADDR", "127.0.0.1:8500"),
        help="Consul address",
    )
    parser.add_argument(
        "--token", default=os.environ.get("CONSUL_TOKEN", ""),
        help="Consul ACL token",
    )
    args = parser.parse_args()

    if not args.actor:
        parser.error("--actor is required when AGENT_ID is not set")
    try:
        content = Path(args.content_file).read_text(encoding="utf-8")
    except OSError as exc:
        parser.error(f"cannot read --content-file: {exc}")

    store = ConsulClient(addr=args.consul, token=args.token)
    try:
        result = RequirementChangeService(store).apply(
            args.req_id,
            content=content,
            reason=args.reason,
            changed_tasks=parse_tasks(args.changed_tasks),
            actor=args.actor,
        )
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc

    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
