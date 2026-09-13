"""Shared cursor pagination helpers for KVStore implementations."""
from __future__ import annotations

import base64
import json
from typing import Any, Iterable, Optional


DEFAULT_KV_LIST_LIMIT = 100
MAX_KV_LIST_LIMIT = 1000


def encode_cursor(prefix: str, key: str) -> str:
    """Encode the last returned key as an opaque URL-safe cursor."""
    payload = json.dumps({"prefix": prefix, "key": key}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: Optional[str], prefix: str) -> Optional[str]:
    if not cursor:
        return None
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(
            (cursor + padding).encode("ascii")
        ).decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("prefix") != prefix:
            raise ValueError("KV list cursor does not match prefix")
        key = payload.get("key")
        if not isinstance(key, str):
            raise ValueError("invalid KV list cursor key")
        return key
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid KV list cursor") from exc


def normalize_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("KV list limit must be an integer")
    if limit < 1 or limit > MAX_KV_LIST_LIMIT:
        raise ValueError(
            f"KV list limit must be between 1 and {MAX_KV_LIST_LIMIT}"
        )
    return limit


def paginate_items(
    items: Iterable[dict[str, Any]],
    *,
    prefix: str,
    cursor: Optional[str],
    limit: int,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Sort normalized items and return the page after ``cursor``."""
    normalized_limit = normalize_limit(limit)
    after_key = decode_cursor(cursor, prefix)
    ordered = sorted(items, key=lambda item: str(item["key"]))
    if after_key is not None:
        ordered = [item for item in ordered if str(item["key"]) > after_key]
    page = ordered[:normalized_limit]
    next_cursor = None
    if len(ordered) > len(page) and page:
        next_cursor = encode_cursor(prefix, str(page[-1]["key"]))
    return page, next_cursor
