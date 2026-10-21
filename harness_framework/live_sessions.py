"""Ephemeral in-memory stream of ACP updates for running tasks.

The durable copy of a conversation lives in the Agent's own session store.
This hub only keeps a bounded ring buffer so the dashboard can watch a task
while it runs; nothing here is persisted, and the buffer disappears with the
daemon process.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from typing import Any


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class LiveSessionHub:
    """Thread-safe, bounded fan-out buffer keyed by ``(scope_id, task)``."""

    def __init__(self, max_events: int = 2000, max_tasks: int = 256):
        self._lock = threading.Lock()
        self._buffers: OrderedDict[tuple[str, str], deque] = OrderedDict()
        self._seq: dict[tuple[str, str], int] = {}
        self._meta: dict[tuple[str, str], dict[str, Any]] = {}
        self.max_events = max(1, max_events)
        self.max_tasks = max(1, max_tasks)

    def publish(
        self, scope_id: str, task: str, *,
        attempt_id: str = "", session_id: str = "", payload: dict[str, Any],
    ) -> int:
        key = (scope_id, task)
        with self._lock:
            buffer = self._buffers.get(key)
            if buffer is None:
                while len(self._buffers) >= self.max_tasks:
                    old_key, _ = self._buffers.popitem(last=False)
                    self._seq.pop(old_key, None)
                    self._meta.pop(old_key, None)
                buffer = deque(maxlen=self.max_events)
                self._buffers[key] = buffer
                self._seq[key] = 0
            else:
                self._buffers.move_to_end(key)
            self._seq[key] += 1
            seq = self._seq[key]
            buffer.append({
                "seq": seq,
                "ts": _now_iso(),
                "attempt_id": attempt_id,
                "session_id": session_id,
                "payload": payload,
            })
            self._meta[key] = {
                "updated_at": time.monotonic(),
                "attempt_id": attempt_id,
                "session_id": session_id,
            }
            return seq

    def snapshot(
        self, scope_id: str, task: str, *, after: int = 0, limit: int = 500,
    ) -> dict[str, Any]:
        key = (scope_id, task)
        limit = max(1, min(int(limit or 500), 2000))
        with self._lock:
            buffer = self._buffers.get(key)
            meta = self._meta.get(key) or {}
            events = [event for event in (buffer or ()) if event["seq"] > after][:limit]
            oldest = buffer[0]["seq"] if buffer else 0
            truncated = bool(buffer) and after > 0 and after < oldest - 1
            return {
                "events": events,
                "cursor": events[-1]["seq"] if events else int(after or 0),
                "active": bool(meta) and time.monotonic() - meta["updated_at"] < 10.0,
                "attempt_id": meta.get("attempt_id", ""),
                "session_id": meta.get("session_id", ""),
                "truncated": truncated,
            }

    def clear(self, scope_id: str, task: str) -> None:
        key = (scope_id, task)
        with self._lock:
            self._buffers.pop(key, None)
            self._seq.pop(key, None)
            self._meta.pop(key, None)
