"""Attempt-fenced resource budgets and durable circuit breakers."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import datetime
import json
from typing import Any

from .kv_store_protocol import KVStore


@dataclass(frozen=True)
class ResourceBudget:
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_tool_calls: int | None = None
    max_wall_clock_seconds: float | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "ResourceBudget":
        value = value or {}
        if not isinstance(value, dict):
            raise ValueError("resource_budget must be an object")
        unknown = set(value) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError("unknown resource_budget fields: " + ", ".join(sorted(unknown)))
        for field in ("max_tokens", "max_tool_calls"):
            limit = value.get(field)
            if limit is not None and (
                isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
            ):
                raise ValueError(f"resource_budget.{field} must be a positive integer")
        for field in ("max_cost_usd", "max_wall_clock_seconds"):
            limit = value.get(field)
            if limit is not None and (
                isinstance(limit, bool) or not isinstance(limit, (int, float)) or limit <= 0
            ):
                raise ValueError(f"resource_budget.{field} must be positive")
        return cls(**value)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BudgetLedger:
    def __init__(self, store: KVStore):
        self.store = store

    def consume(
        self, req_id: str, task_name: str, *, attempt_id: str, lease_epoch: int,
        tokens: int = 0, cost_usd: float = 0, tool_calls: int = 0,
        wall_clock_seconds: float = 0,
    ) -> dict[str, Any]:
        base = f"workflows/{req_id}/tasks/{task_name}"
        current_attempt, _ = self.store.kv_get(f"{base}/attempt_id")
        current_epoch, _ = self.store.kv_get(f"{base}/lease_epoch")
        if current_attempt != attempt_id or str(current_epoch) != str(lease_epoch):
            raise PermissionError("stale task attempt; budget write fenced")
        increments = {
            "tokens": tokens, "cost_usd": cost_usd,
            "tool_calls": tool_calls, "wall_clock_seconds": wall_clock_seconds,
        }
        for name, amount in increments.items():
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0:
                raise ValueError(f"{name} increment must be non-negative")
        budget_raw, _ = self.store.kv_get(f"{base}/resource_budget")
        budget = ResourceBudget.from_dict(json.loads(budget_raw) if budget_raw else None)
        usage_key = f"{base}/budget/usage"
        usage_raw, usage_index = self.store.kv_get(usage_key)
        usage = json.loads(usage_raw) if usage_raw else {
            "tokens": 0, "cost_usd": 0.0, "tool_calls": 0,
            "wall_clock_seconds": 0.0,
        }
        updated = {name: usage.get(name, 0) + amount for name, amount in increments.items()}
        exceeded = []
        for usage_name, limit_name in (
            ("tokens", "max_tokens"), ("cost_usd", "max_cost_usd"),
            ("tool_calls", "max_tool_calls"),
            ("wall_clock_seconds", "max_wall_clock_seconds"),
        ):
            limit = getattr(budget, limit_name)
            if limit is not None and updated[usage_name] > limit:
                exceeded.append(limit_name)
        usage_cas = usage_index if usage_raw is not None else 0
        if not self.store.kv_put(usage_key, json.dumps(updated, sort_keys=True), cas=usage_cas):
            raise RuntimeError("concurrent budget update detected")
        status = "TRIPPED" if exceeded else "ALLOW"
        if exceeded:
            breaker = {
                "status": "OPEN", "exceeded": exceeded,
                "opened_at": _now_iso(), "attempt_id": attempt_id,
                "lease_epoch": int(lease_epoch), "usage": updated,
            }
            self.store.kv_put(f"{base}/budget/circuit_breaker", json.dumps(breaker, sort_keys=True))
        return {"status": status, "usage": updated, "exceeded": exceeded}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
