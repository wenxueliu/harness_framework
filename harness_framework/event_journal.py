"""Canonical event envelope used by the Dashboard event journal and SSE."""
from __future__ import annotations

import datetime
import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .kv_store_protocol import KVStore


@dataclass(frozen=True)
class EventEnvelope:
    event_id: str
    sequence: int
    type: str
    occurred_at: str
    subject: Mapping[str, str]
    actor: Mapping[str, str]
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_id or not self.type:
            raise ValueError("event_id and type are required")
        if self.sequence < 1:
            raise ValueError("event sequence must be positive")
        if not self.subject:
            raise ValueError("event subject is required")
        if not self.actor.get("type") or not self.actor.get("id"):
            raise ValueError("event actor type and id are required")
        try:
            parsed = datetime.datetime.fromisoformat(
                self.occurred_at.replace("Z", "+00:00")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("occurred_at must be RFC 3339") from exc
        if parsed.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventJournal:
    """Append-only global event stream backed by KVStore CAS."""

    def __init__(
        self, store: KVStore, stream_id: str = "global", *, retention_events: int = 10000
    ):
        self.store = store
        self.stream_id = stream_id
        self.base = f"events/{stream_id}"
        self.retention_events = max(100, int(retention_events))

    def append(
        self, event_type: str, *, subject: Mapping[str, str],
        actor: Mapping[str, str], data: Mapping[str, Any] | None = None,
    ) -> EventEnvelope:
        for _ in range(100):
            current, index = self.store.kv_get(f"{self.base}/head")
            sequence = int(current or "0") + 1
            if self.store.kv_put(
                f"{self.base}/head", str(sequence), cas=index if current else 0
            ):
                break
        else:
            raise RuntimeError("could not allocate event sequence")
        event = EventEnvelope(
            event_id=f"evt_{uuid.uuid4().hex}", sequence=sequence,
            type=event_type,
            occurred_at=datetime.datetime.now(datetime.timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
            subject=dict(subject), actor=dict(actor), data=dict(data or {}),
        )
        key = f"{self.base}/records/{sequence:020d}"
        if not self.store.kv_put(key, json.dumps(event.to_dict(), ensure_ascii=False), cas=0):
            raise RuntimeError("event sequence record already exists")
        self.store.kv_put(f"{self.base}/ids/{event.event_id}", str(sequence), cas=0)
        self._trim(sequence)
        return event

    def _trim(self, head: int) -> None:
        cutoff = head - self.retention_events
        if cutoff <= 0:
            return
        # KVStore has no range delete; deleting one old record at a time keeps
        # Local/File/Consul behavior identical and makes cursor expiry explicit.
        trimmed_raw, _ = self.store.kv_get(f"{self.base}/trimmed_until")
        trimmed_until = int(trimmed_raw or "0")
        for sequence in range(trimmed_until + 1, cutoff + 1):
            key = f"{self.base}/records/{sequence:020d}"
            raw, _ = self.store.kv_get(key)
            if not raw:
                continue
            try:
                event_id = json.loads(raw).get("event_id")
            except (TypeError, json.JSONDecodeError):
                event_id = None
            self.store.kv_delete(key)
            if event_id:
                self.store.kv_delete(f"{self.base}/ids/{event_id}")
        self.store.kv_put(f"{self.base}/trimmed_until", str(cutoff))

    def replay(
        self, *, after_event_id: str | None = None, limit: int = 500
    ) -> tuple[list[EventEnvelope], bool]:
        after_sequence = 0
        reset_required = False
        if after_event_id:
            raw, _ = self.store.kv_get(f"{self.base}/ids/{after_event_id}")
            if raw is None:
                return [], True
            after_sequence = int(raw)
        events = []
        cursor = None
        page_limit = 1000
        requested = max(1, min(limit, 1000))
        while len(events) < requested:
            items, cursor = self.store.kv_list(
                f"{self.base}/records/", cursor=cursor, limit=page_limit
            )
            for item in items:
                try:
                    data = json.loads(item["value"])
                    if int(data["sequence"]) <= after_sequence:
                        continue
                    events.append(EventEnvelope(**data))
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if len(events) >= requested:
                    break
            if cursor is None:
                break
        events.sort(key=lambda event: event.sequence)
        return events[:requested], reset_required

    def head(self) -> tuple[int, int]:
        raw, index = self.store.kv_get(f"{self.base}/head")
        return int(raw or "0"), index
