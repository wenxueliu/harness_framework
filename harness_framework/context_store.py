"""Typed workflow context namespaces with explicit mutability boundaries."""
from __future__ import annotations

import datetime
import hashlib
import json
import time
import uuid
from typing import Any

from .kv_store_protocol import KVStore


CONTEXT_NAMESPACES = frozenset({
    "facts", "artifacts", "working_memory", "events", "summaries", "restricted",
})


class ContextStore:
    """Persist workflow knowledge without mixing records of different trust."""

    def __init__(self, store: KVStore):
        self.store = store

    def put_fact(self, req_id: str, key: str, value: Any, *, actor: str) -> str:
        """Create an immutable fact. Existing facts cannot be overwritten."""
        path = f"{_root(req_id)}/facts/{_key(key)}"
        record = _record(value, actor=actor, record_type="fact")
        if not self.store.kv_put(path, _json(record), cas=0):
            raise ValueError(f"immutable fact already exists: {key}")
        return path

    def publish_artifact(
        self, req_id: str, key: str, value: Any, *, actor: str,
        lineage: list[str] | None = None,
    ) -> dict[str, Any]:
        """Publish an immutable artifact version and advance its CAS pointer."""
        base = f"{_root(req_id)}/artifacts/{_key(key)}"
        pointer_raw, pointer_index = self.store.kv_get(f"{base}/current")
        pointer = json.loads(pointer_raw) if pointer_raw else {}
        revision = int(pointer.get("revision", 0)) + 1
        version_id = f"v{revision}-{uuid.uuid4().hex}"
        encoded_value = _json(value)
        metadata = {
            "revision": revision,
            "version_id": version_id,
            "checksum": "sha256:" + hashlib.sha256(encoded_value.encode()).hexdigest(),
            "created_at": _now_iso(),
            "created_by": _actor(actor),
            "lineage": list(lineage or []),
        }
        version_base = f"{base}/versions/{version_id}"
        self.store.kv_put(f"{version_base}/value", encoded_value)
        self.store.kv_put(f"{version_base}/metadata", _json(metadata))
        pointer_cas = pointer_index if pointer_raw is not None else 0
        if not self.store.kv_put(f"{base}/current", _json(metadata), cas=pointer_cas):
            raise RuntimeError("concurrent context artifact publication detected")
        return metadata

    def put_working_memory(
        self, req_id: str, task_name: str, key: str, value: Any, *, actor: str
    ) -> str:
        path = f"{_root(req_id)}/working_memory/{_key(task_name)}/{_key(key)}"
        self.store.kv_put(path, _json(_record(value, actor=actor, record_type="working_memory")))
        return path

    def append_event(
        self, req_id: str, task_name: str, event: dict[str, Any], *, actor: str
    ) -> str:
        if not isinstance(event, dict) or not event:
            raise ValueError("event must be a non-empty object")
        seq = f"{int(time.time() * 1000000):021d}-{uuid.uuid4().hex[:8]}"
        path = f"{_root(req_id)}/events/{_key(task_name)}/{seq}"
        if not self.store.kv_put(
            path, _json(_record(event, actor=actor, record_type="event")), cas=0
        ):
            raise RuntimeError("event id collision")
        return path

    def put_summary(
        self, req_id: str, key: str, value: Any, *, actor: str,
        source_refs: list[str], full_value: dict[str, Any],
        preserved_fields: list[str], max_bytes: int,
    ) -> str:
        if not source_refs:
            raise ValueError("derived summary requires source_refs")
        if not isinstance(full_value, dict):
            raise ValueError("full_value must be an object")
        if not preserved_fields or not all(
            isinstance(field, str) and field.strip() for field in preserved_fields
        ):
            raise ValueError("preserved_fields must be a non-empty list of strings")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        if not isinstance(value, dict):
            raise ValueError("bounded summary value must be an object")
        for field_path in preserved_fields:
            expected = _field_value(full_value, field_path)
            actual = _field_value(value, field_path)
            if actual != expected:
                raise ValueError(f"summary did not preserve mandatory field: {field_path}")
        encoded_value = _json(value).encode("utf-8")
        if len(encoded_value) > max_bytes:
            raise ValueError(
                f"summary exceeds max_bytes: {len(encoded_value)} > {max_bytes}"
            )
        path = f"{_root(req_id)}/summaries/{_key(key)}"
        record = _record(value, actor=actor, record_type="summary")
        record["source_refs"] = list(source_refs)
        record["preserved_fields"] = list(preserved_fields)
        record["max_bytes"] = max_bytes
        record["size_bytes"] = len(encoded_value)
        self.store.kv_put(path, _json(record))
        return path

    def put_restricted(
        self, req_id: str, key: str, value: Any, *, actor: str,
        classification: str,
    ) -> str:
        if classification not in {"CONFIDENTIAL", "SECRET"}:
            raise ValueError("restricted classification must be CONFIDENTIAL or SECRET")
        path = f"{_root(req_id)}/restricted/{_key(key)}"
        record = _record(value, actor=actor, record_type="restricted")
        record["classification"] = classification
        self.store.kv_put(path, _json(record))
        return path

    def read_namespace(
        self, req_id: str, namespace: str, *, allow_restricted: bool = False
    ) -> dict[str, Any]:
        if namespace not in CONTEXT_NAMESPACES:
            raise ValueError(f"unknown context namespace: {namespace}")
        if namespace == "restricted" and not allow_restricted:
            raise PermissionError("restricted context requires explicit authorization")
        base = f"{_root(req_id)}/{namespace}/"
        items, _ = self.store.kv_get(base, recurse=True)
        return {
            item["Key"][len(base):]: json.loads(item.get("_decoded", "null"))
            for item in items or []
        }


def _root(req_id: str) -> str:
    if not req_id:
        raise ValueError("req_id is required")
    return f"workflows/{req_id}/knowledge"


def _key(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or value.startswith("/"):
        raise ValueError("context key must be a non-empty relative path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("context key contains an invalid path segment")
    return value


def _actor(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("actor is required")
    return value


def _record(value: Any, *, actor: str, record_type: str) -> dict[str, Any]:
    return {
        "type": record_type,
        "value": value,
        "recorded_at": _now_iso(),
        "recorded_by": _actor(actor),
    }


def _field_value(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"mandatory field is missing: {path}")
        current = current[part]
    return current


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("context value must be JSON serializable") from exc


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
