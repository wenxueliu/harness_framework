"""Deterministic recovery paths from structured failure envelopes."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import datetime
import json
import uuid
from typing import Any

from .incremental import affected_downstream_closure, invalidate_impacted_tasks
from .kv_store_protocol import KVStore


@dataclass(frozen=True)
class RecoveryPolicy:
    primary_action: str = "retry_same_strategy"
    narrowed_action: str = "retry_narrowed_scope"
    degraded_action: str = "continue_degraded"
    human_target: str = "human"
    primary_attempts: int = 1
    narrowed_attempts: int = 1
    degraded_attempts: int = 1

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "RecoveryPolicy":
        value = value or {}
        if not isinstance(value, dict):
            raise ValueError("recovery_policy must be an object")
        unknown = set(value) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError("unknown recovery_policy fields: " + ", ".join(sorted(unknown)))
        for field in (
            "primary_action", "narrowed_action", "degraded_action", "human_target",
        ):
            candidate = value.get(field, getattr(cls(), field))
            if not isinstance(candidate, str) or not candidate.strip():
                raise ValueError(f"recovery_policy.{field} must be a non-empty string")
        for field in ("primary_attempts", "narrowed_attempts", "degraded_attempts"):
            candidate = value.get(field, getattr(cls(), field))
            if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 0:
                raise ValueError(f"recovery_policy.{field} must be a non-negative integer")
        return cls(**value)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RecoveryDecision:
    path: str
    action: str
    escalation_target: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def select_recovery_path(
    policy: RecoveryPolicy, failure: dict[str, Any], attempts_used: int,
) -> RecoveryDecision:
    if isinstance(attempts_used, bool) or not isinstance(attempts_used, int) or attempts_used < 0:
        raise ValueError("attempts_used must be a non-negative integer")
    severity = failure.get("severity", "HIGH")
    retryable = failure.get("retryable", False)
    failure_type = failure.get("failure_type", "HARD")
    if severity == "CRITICAL":
        return RecoveryDecision(
            "HUMAN", "escalate", policy.human_target, "critical_failure",
        )
    if not retryable and failure_type not in {"PARTIAL"}:
        return RecoveryDecision(
            "HUMAN", "escalate", policy.human_target, "non_retryable_failure",
        )
    primary_end = policy.primary_attempts
    narrowed_end = primary_end + policy.narrowed_attempts
    degraded_end = narrowed_end + policy.degraded_attempts
    if attempts_used < primary_end:
        return RecoveryDecision("PRIMARY", policy.primary_action, reason="primary_budget")
    if attempts_used < narrowed_end:
        return RecoveryDecision("NARROWED", policy.narrowed_action, reason="primary_exhausted")
    if attempts_used < degraded_end:
        return RecoveryDecision("DEGRADED", policy.degraded_action, reason="fallback_exhausted")
    return RecoveryDecision(
        "HUMAN", "escalate", policy.human_target, "all_automatic_paths_exhausted",
    )


def task_ancestors(
    dependencies: dict[str, dict[str, Any]], task_name: str,
) -> set[str]:
    """Return the task and all of its transitive upstream dependencies."""
    if task_name not in dependencies:
        raise ValueError(f"unknown task: {task_name}")
    ancestors = {task_name}
    queue = [task_name]
    while queue:
        current = queue.pop(0)
        for raw in dependencies[current].get("depends_on", []):
            upstream = raw.get("task", "") if isinstance(raw, dict) else raw
            if upstream in dependencies and upstream not in ancestors:
                ancestors.add(upstream)
                queue.append(upstream)
    return ancestors


def validate_recovery_target(
    dependencies: dict[str, dict[str, Any]], current_task: str,
    target_task: str, allowed_targets: list[str] | None = None,
) -> None:
    """Require a configured recovery target on the current task's ancestry."""
    if target_task not in task_ancestors(dependencies, current_task):
        raise ValueError(
            f"recovery target '{target_task}' is not the current task or an ancestor"
        )
    if allowed_targets and target_task not in allowed_targets:
        raise ValueError(f"recovery target '{target_task}' is not allowed")


def rewind_to_task(
    store: KVStore, req_id: str, current_task: str, target_task: str,
    feedback: dict[str, Any], *, actor: str,
    allowed_targets: list[str] | None = None, run_manager: Any = None,
) -> dict[str, Any]:
    """Invalidate a target and its downstream, then make the target runnable."""
    dependencies_raw, _ = store.kv_get(f"workflows/{req_id}/dependencies")
    if not dependencies_raw:
        raise ValueError("workflow has no dependencies")
    try:
        dependencies = json.loads(dependencies_raw)
    except json.JSONDecodeError as exc:
        raise ValueError("workflow dependencies are invalid JSON") from exc
    validate_recovery_target(
        dependencies, current_task, target_task, allowed_targets,
    )

    lock_key = f"workflows/{req_id}/recovery_lock"
    recovery_id = f"recovery-{uuid.uuid4().hex}"
    if not store.kv_put(lock_key, recovery_id, cas=0):
        raise RuntimeError("another recovery is in progress")
    try:
        impacted = affected_downstream_closure(dependencies, [target_task])
        previous = {
            task: store.kv_get(
                f"workflows/{req_id}/tasks/{task}/status"
            )[0] or ""
            for task in impacted
        }
        invalidate_impacted_tasks(
            store, req_id, dependencies, [target_task], change_id=recovery_id,
        )
        record = {
            "recovery_id": recovery_id,
            "source_task": current_task,
            "target_task": target_task,
            "actor": actor,
            "feedback": feedback,
            "created_at": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat().replace("+00:00", "Z"),
        }
        target_base = f"workflows/{req_id}/tasks/{target_task}"
        store.kv_put(
            f"{target_base}/recovery_feedback/current",
            json.dumps(record, ensure_ascii=False),
        )
        store.kv_put(
            f"{target_base}/recovery_feedback/history/{recovery_id}",
            json.dumps(record, ensure_ascii=False),
        )
        if run_manager is not None:
            run_id = run_manager.get_or_create_run(req_id, actor)
            for task in sorted(impacted):
                new_status = store.kv_get(
                    f"workflows/{req_id}/tasks/{task}/status"
                )[0] or ""
                run_manager.record_transition(
                    req_id, run_id, task, previous[task], new_status, actor,
                    reason=f"rewind to {target_task}",
                    metadata={"recovery_id": recovery_id,
                              "source_task": current_task},
                )
        return {**record, "impacted_tasks": sorted(impacted)}
    finally:
        store.kv_delete(lock_key)
