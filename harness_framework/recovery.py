"""Deterministic recovery paths from structured failure envelopes."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


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
