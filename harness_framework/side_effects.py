"""Idempotency ledger and compensation activation for side-effecting tasks."""
from __future__ import annotations

import datetime
import hashlib
import json
from typing import Any

from .kv_store_protocol import KVStore


class IdempotencyConflict(RuntimeError):
    pass


class SideEffectLedger:
    def __init__(self, store: KVStore):
        self.store = store

    def begin(
        self, req_id: str, task_name: str, idempotency_key: str, *,
        attempt_id: str, lease_epoch: int,
    ) -> dict[str, Any]:
        base = self._validate_owner(req_id, task_name, attempt_id, lease_epoch)
        key_hash = _key_hash(idempotency_key)
        record_key = f"{base}/side_effects/{key_hash}/record"
        raw, _ = self.store.kv_get(record_key)
        if raw:
            record = json.loads(raw)
            if record["idempotency_key"] != idempotency_key:
                raise IdempotencyConflict("idempotency hash collision")
            if record["status"] == "COMPLETED":
                return {"action": "REPLAY", "record": record}
            if (record["status"] == "IN_PROGRESS"
                    and record["attempt_id"] == attempt_id
                    and int(record["lease_epoch"]) == int(lease_epoch)):
                return {"action": "RESUME", "record": record}
            if record["status"] in {"FAILED", "COMPENSATING"}:
                return {"action": "WAIT_FOR_COMPENSATION", "record": record}
            if record["status"] == "COMPENSATED":
                return {"action": "COMPENSATED", "record": record}
            raise IdempotencyConflict("idempotency key is owned by another attempt")

        record = {
            "idempotency_key": idempotency_key,
            "status": "IN_PROGRESS",
            "attempt_id": attempt_id,
            "lease_epoch": int(lease_epoch),
            "started_at": _now_iso(),
        }
        if not self.store.kv_put(record_key, json.dumps(record, sort_keys=True), cas=0):
            raise IdempotencyConflict("concurrent idempotency key acquisition")
        return {"action": "EXECUTE", "record": record}

    def complete(
        self, req_id: str, task_name: str, idempotency_key: str, *,
        attempt_id: str, lease_epoch: int, result: dict[str, Any],
    ) -> dict[str, Any]:
        return self._finish(
            req_id, task_name, idempotency_key, attempt_id=attempt_id,
            lease_epoch=lease_epoch, status="COMPLETED", extra={"result": result},
        )

    def fail_and_compensate(
        self, req_id: str, task_name: str, idempotency_key: str, *,
        attempt_id: str, lease_epoch: int, error: str,
    ) -> dict[str, Any]:
        base = self._validate_owner(req_id, task_name, attempt_id, lease_epoch)
        record = self._finish(
            req_id, task_name, idempotency_key, attempt_id=attempt_id,
            lease_epoch=lease_epoch, status="FAILED", extra={"error": error},
        )
        compensation_task, _ = self.store.kv_get(f"{base}/compensation_task")
        if not compensation_task:
            raise ValueError("side-effecting task has no compensation_task")
        compensation_base = f"workflows/{req_id}/tasks/{compensation_task}"
        status, status_index = self.store.kv_get(f"{compensation_base}/status")
        if status != "BLOCKED":
            raise IdempotencyConflict(
                f"compensation task status is {status}, expected BLOCKED"
            )
        self.store.kv_put(f"{compensation_base}/compensates_task", task_name)
        self.store.kv_put(f"{compensation_base}/idempotency_key", idempotency_key)
        if not self.store.kv_put(
            f"{compensation_base}/status", "PENDING", cas=status_index
        ):
            raise IdempotencyConflict("concurrent compensation activation")
        record["compensation_task"] = compensation_task
        return record

    def mark_compensated(
        self, req_id: str, source_task: str, idempotency_key: str, *,
        compensation_attempt_id: str, compensation_lease_epoch: int,
        compensation_task: str,
    ) -> dict[str, Any]:
        self._validate_owner(
            req_id, compensation_task, compensation_attempt_id,
            compensation_lease_epoch,
        )
        source_base = f"workflows/{req_id}/tasks/{source_task}"
        record_key = f"{source_base}/side_effects/{_key_hash(idempotency_key)}/record"
        raw, index = self.store.kv_get(record_key)
        if not raw:
            raise KeyError("side-effect record not found")
        record = json.loads(raw)
        if record.get("status") != "FAILED":
            raise ValueError("only FAILED side effects can be compensated")
        record.update({
            "status": "COMPENSATED", "compensated_at": _now_iso(),
            "compensation_task": compensation_task,
            "compensation_attempt_id": compensation_attempt_id,
        })
        if not self.store.kv_put(record_key, json.dumps(record, sort_keys=True), cas=index):
            raise IdempotencyConflict("concurrent compensation completion")
        return record

    def _finish(
        self, req_id: str, task_name: str, idempotency_key: str, *,
        attempt_id: str, lease_epoch: int, status: str, extra: dict[str, Any],
    ) -> dict[str, Any]:
        base = self._validate_owner(req_id, task_name, attempt_id, lease_epoch)
        record_key = f"{base}/side_effects/{_key_hash(idempotency_key)}/record"
        raw, index = self.store.kv_get(record_key)
        if not raw:
            raise KeyError("side-effect record not found")
        record = json.loads(raw)
        if record.get("idempotency_key") != idempotency_key:
            raise IdempotencyConflict("idempotency hash collision")
        if record.get("status") == "COMPLETED" and status == "COMPLETED":
            return record
        if (record.get("status") != "IN_PROGRESS"
                or record.get("attempt_id") != attempt_id):
            raise IdempotencyConflict("side effect is not owned by current attempt")
        record.update(extra)
        record["status"] = status
        record["finished_at"] = _now_iso()
        if not self.store.kv_put(record_key, json.dumps(record, sort_keys=True), cas=index):
            raise IdempotencyConflict("concurrent side-effect completion")
        return record

    def _validate_owner(
        self, req_id: str, task_name: str, attempt_id: str, lease_epoch: int
    ) -> str:
        base = f"workflows/{req_id}/tasks/{task_name}"
        current_attempt, _ = self.store.kv_get(f"{base}/attempt_id")
        current_epoch, _ = self.store.kv_get(f"{base}/lease_epoch")
        if current_attempt != attempt_id or str(current_epoch) != str(lease_epoch):
            raise PermissionError("stale task attempt; side effect fenced")
        return base


def _key_hash(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("idempotency_key is required")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
