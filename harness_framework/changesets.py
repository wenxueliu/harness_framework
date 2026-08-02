"""Audited ChangeSet lifecycle for incremental workflow delivery."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import datetime
import json
import time
import uuid
from typing import Any

from .kv_store_protocol import KVStore


CHANGESET_STATES = frozenset({
    "PROPOSED", "IMPACT_ANALYZED", "APPROVED", "APPLIED", "REJECTED",
    "SUPERSEDED",
})
CHANGESET_TERMINAL_STATES = frozenset({"APPLIED", "REJECTED", "SUPERSEDED"})
_TRANSITIONS = {
    "PROPOSED": frozenset({"IMPACT_ANALYZED", "REJECTED", "SUPERSEDED"}),
    "IMPACT_ANALYZED": frozenset({"APPROVED", "REJECTED", "SUPERSEDED"}),
    "APPROVED": frozenset({"APPLIED", "SUPERSEDED"}),
    "APPLIED": frozenset(),
    "REJECTED": frozenset(),
    "SUPERSEDED": frozenset(),
}


class ChangeSetConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class ChangeSet:
    change_id: str
    status: str
    proposed_by: str
    proposed_at: str
    base_versions: dict[str, str]
    changes: dict[str, Any]
    impact_analysis: dict[str, Any] = field(default_factory=dict)
    decided_by: str = ""
    decided_at: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ChangeSetStore:
    def __init__(self, store: KVStore):
        self.store = store

    def propose(
        self, req_id: str, *, changes: dict[str, Any],
        base_versions: dict[str, str], actor: str,
    ) -> ChangeSet:
        if not isinstance(changes, dict) or not changes:
            raise ValueError("changes must be a non-empty object")
        if not isinstance(base_versions, dict):
            raise ValueError("base_versions must be an object")
        change = ChangeSet(
            change_id=f"chg-{uuid.uuid4().hex}",
            status="PROPOSED",
            proposed_by=_non_empty(actor, "actor"),
            proposed_at=_now_iso(),
            base_versions=dict(base_versions),
            changes=dict(changes),
        )
        key = self._record_key(req_id, change.change_id)
        if not self.store.kv_put(
            key, json.dumps(change.to_dict(), ensure_ascii=False, sort_keys=True), cas=0
        ):
            raise ChangeSetConflict("changeset id collision")
        self._append_history(req_id, change, actor, "created")
        return change

    def get(self, req_id: str, change_id: str) -> ChangeSet | None:
        raw, _ = self.store.kv_get(self._record_key(req_id, change_id))
        if not raw:
            return None
        return ChangeSet(**json.loads(raw))

    def transition(
        self, req_id: str, change_id: str, new_status: str, *, actor: str,
        reason: str = "", impact_analysis: dict[str, Any] | None = None,
    ) -> ChangeSet:
        if new_status not in CHANGESET_STATES:
            raise ValueError(f"invalid changeset status: {new_status}")
        key = self._record_key(req_id, change_id)
        raw, index = self.store.kv_get(key)
        if not raw:
            raise KeyError(f"changeset not found: {change_id}")
        current = ChangeSet(**json.loads(raw))
        if new_status not in _TRANSITIONS[current.status]:
            raise ValueError(f"invalid changeset transition: {current.status} -> {new_status}")
        analysis = current.impact_analysis
        if new_status == "IMPACT_ANALYZED":
            if not isinstance(impact_analysis, dict) or not impact_analysis:
                raise ValueError("impact_analysis is required")
            analysis = dict(impact_analysis)
        if new_status == "APPROVED" and not analysis:
            raise ValueError("changeset cannot be approved without impact analysis")

        now = _now_iso()
        decision = new_status in {"APPROVED", "APPLIED", "REJECTED", "SUPERSEDED"}
        updated = ChangeSet(
            change_id=current.change_id,
            status=new_status,
            proposed_by=current.proposed_by,
            proposed_at=current.proposed_at,
            base_versions=current.base_versions,
            changes=current.changes,
            impact_analysis=analysis,
            decided_by=_non_empty(actor, "actor") if decision else current.decided_by,
            decided_at=now if decision else current.decided_at,
            reason=reason or current.reason,
        )
        if not self.store.kv_put(
            key, json.dumps(updated.to_dict(), ensure_ascii=False, sort_keys=True),
            cas=index,
        ):
            raise ChangeSetConflict("concurrent changeset transition detected")
        self._append_history(req_id, updated, actor, reason)
        return updated

    @staticmethod
    def _record_key(req_id: str, change_id: str) -> str:
        return f"workflows/{req_id}/changesets/{change_id}/record"

    def _append_history(
        self, req_id: str, change: ChangeSet, actor: str, reason: str
    ) -> None:
        seq = f"{int(time.time() * 1000000):021d}-{uuid.uuid4().hex[:8]}"
        event = {
            "change_id": change.change_id,
            "status": change.status,
            "actor": actor,
            "reason": reason,
            "observed_at": _now_iso(),
        }
        self.store.kv_put(
            f"workflows/{req_id}/changesets/{change.change_id}/history/{seq}",
            json.dumps(event, ensure_ascii=False, sort_keys=True),
        )


def _non_empty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
