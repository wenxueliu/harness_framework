#!/usr/bin/env python3
"""Host-hook adapter for Harness adaptive-control boundaries.

The script is platform neutral: Claude Code, Codex, or another host can invoke
it from user-message, pre-tool, and post-tool hooks.  It talks only to the
Harness WebAPI and never owns orchestration state itself.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="adaptive-boundary")
    value.add_argument("event", choices=("user", "pre-tool", "post-tool"))
    value.add_argument("--req-id", default=os.environ.get("REQ_ID", ""))
    value.add_argument("--task", default=os.environ.get("TASK_NAME", ""))
    value.add_argument("--actor", default=os.environ.get("AGENT_ID", "host"))
    value.add_argument("--message", default="")
    value.add_argument("--api", default=os.environ.get("HARNESS_API", "http://127.0.0.1:8080"))
    value.add_argument("--control-operation", action="store_true")
    return value


def boundary_exit_code(boundary: dict, *, control_operation: bool = False) -> int:
    if control_operation:
        return 0
    kind = boundary.get("kind")
    if kind == "ABORT":
        return 7
    if boundary.get("blocked"):
        return 6
    return 0


def request_json(url: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": "boundary_unavailable", "message": str(exc)}
    return value if isinstance(value, dict) else {"ok": False, "error": "invalid_response"}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.req_id or not args.task:
        print(json.dumps({"ok": False, "error": "REQ_ID and TASK_NAME are required"}))
        return 2
    root = args.api.rstrip("/")
    path = "/api/workflow/{}/task/{}/adaptive".format(
        quote(args.req_id, safe=""), quote(args.task, safe=""),
    )
    if args.event == "user":
        if not args.message.strip():
            print(json.dumps({"ok": False, "error": "message is required"}))
            return 2
        result = request_json(root + path + "/feedback", {
            "message": args.message, "actor": args.actor,
            "source": {"host_event": "UserPromptSubmit"},
        })
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("feedback_id") else 3

    query = urlencode({"actor": args.actor})
    boundary = request_json(root + path + "/boundary?" + query)
    print(json.dumps(boundary, ensure_ascii=False))
    if boundary.get("error"):
        return 3
    return boundary_exit_code(boundary, control_operation=args.control_operation)


if __name__ == "__main__":
    raise SystemExit(main())
