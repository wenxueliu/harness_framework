"""Durable human-to-task messages consumed by ACP task sessions."""
from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

from .kv_store_protocol import KVStore


MESSAGE_MODES = frozenset({"queue", "interrupt"})
TERMINAL_MESSAGE_STATES = frozenset({"APPLIED", "INTERRUPTED", "FAILED"})


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _message_prefix(req_id: str, task_name: str) -> str:
    return f"workflows/{req_id}/tasks/{task_name}/human/messages/"


def create_human_message(
    store: KVStore,
    req_id: str,
    task_name: str,
    *,
    message: str,
    actor: str,
    mode: str = "queue",
) -> dict[str, Any]:
    message = message.strip() if isinstance(message, str) else ""
    actor = actor.strip() if isinstance(actor, str) else ""
    mode = mode.lower() if isinstance(mode, str) else ""
    if not message:
        raise ValueError("message is required")
    if not actor:
        raise ValueError("actor is required")
    if mode not in MESSAGE_MODES:
        raise ValueError("mode must be queue or interrupt")

    item = {
        "message_id": f"HM-{uuid.uuid4().hex[:12].upper()}",
        "message": message,
        "actor": actor,
        "mode": mode,
        "status": "PENDING",
        "created_at": _now_iso(),
    }
    store.kv_put(
        _message_prefix(req_id, task_name) + item["message_id"],
        json.dumps(item, ensure_ascii=False),
    )
    return item


def list_human_messages(
    store: KVStore,
    req_id: str,
    task_name: str,
    *,
    pending_only: bool = False,
) -> list[dict[str, Any]]:
    items, _ = store.kv_get(_message_prefix(req_id, task_name), recurse=True)
    result: list[dict[str, Any]] = []
    for raw in items or []:
        try:
            item = json.loads(raw.get("_decoded", "{}"))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(item, dict) or not item.get("message_id"):
            continue
        if pending_only and item.get("status") != "PENDING":
            continue
        result.append(item)
    result.sort(key=lambda item: (str(item.get("created_at", "")), item["message_id"]))
    return result


def has_pending_interrupt(store: KVStore, req_id: str, task_name: str) -> bool:
    return any(
        item.get("mode") == "interrupt"
        for item in list_human_messages(store, req_id, task_name, pending_only=True)
    )


def claim_next_human_message(
    store: KVStore,
    req_id: str,
    task_name: str,
) -> dict[str, Any] | None:
    pending = list_human_messages(store, req_id, task_name, pending_only=True)
    pending.sort(key=lambda item: (
        0 if item.get("mode") == "interrupt" else 1,
        str(item.get("created_at", "")),
        item["message_id"],
    ))
    for item in pending:
        key = _message_prefix(req_id, task_name) + item["message_id"]
        raw, index = store.kv_get(key)
        if not raw:
            continue
        try:
            current = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if current.get("status") != "PENDING":
            continue
        current.update({"status": "PROCESSING", "started_at": _now_iso()})
        if store.kv_put(key, json.dumps(current, ensure_ascii=False), cas=index):
            return current
    return None


def finish_human_message(
    store: KVStore,
    req_id: str,
    task_name: str,
    item: dict[str, Any],
    *,
    status: str,
    response: str = "",
    error: str = "",
) -> dict[str, Any]:
    if status not in TERMINAL_MESSAGE_STATES:
        raise ValueError("invalid terminal human message status")
    key = _message_prefix(req_id, task_name) + str(item["message_id"])
    raw, index = store.kv_get(key)
    current = json.loads(raw) if raw else dict(item)
    current.update({"status": status, "finished_at": _now_iso()})
    if response:
        current["response"] = response
    if error:
        current["error"] = error
    store.kv_put(key, json.dumps(current, ensure_ascii=False), cas=index)
    return current
