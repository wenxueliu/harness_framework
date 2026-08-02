"""Versioned, validated contracts shared by workflow producers and agents."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
from typing import Any


@dataclass(frozen=True)
class AgentContract:
    """The bounded work agreement attached to an executable task."""

    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    context_budget: int = 0

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "AgentContract":
        value = value or {}
        if not isinstance(value, dict):
            raise ValueError("agent_contract must be an object")
        parsed: dict[str, Any] = {}
        for name in (
            "inputs", "outputs", "responsibilities", "exclusions", "permissions"
        ):
            items = value.get(name, [])
            if not isinstance(items, list) or not all(
                isinstance(item, str) and item.strip() for item in items
            ):
                raise ValueError(f"agent_contract.{name} must be a list of strings")
            parsed[name] = list(items)
        budget = value.get("context_budget", 0)
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
            raise ValueError("agent_contract.context_budget must be a non-negative integer")
        parsed["context_budget"] = budget
        return cls(**parsed)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactManifest:
    """Immutable metadata for one version of a task artifact."""

    schema_version: str
    artifact_version: int
    key: str
    producer_attempt_id: str
    producer_lease_epoch: int
    checksum: str
    size_bytes: int
    created_at: str
    lineage: list[str] = field(default_factory=list)
    validation_status: str = "UNVALIDATED"
    retention: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, *, artifact_version: int, key: str, value: str,
               attempt_id: str, lease_epoch: int, created_at: str,
               lineage: list[str] | None = None,
               validation_status: str = "UNVALIDATED",
               retention: dict[str, Any] | None = None) -> "ArtifactManifest":
        if artifact_version < 1:
            raise ValueError("artifact_version must be positive")
        if validation_status not in {
            "UNVALIDATED", "VALID", "INVALID", "SUPERSEDED"
        }:
            raise ValueError("invalid artifact validation_status")
        encoded = value.encode("utf-8")
        return cls(
            schema_version="1.0",
            artifact_version=artifact_version,
            key=key,
            producer_attempt_id=attempt_id,
            producer_lease_epoch=int(lease_epoch),
            checksum="sha256:" + hashlib.sha256(encoded).hexdigest(),
            size_bytes=len(encoded),
            created_at=created_at,
            lineage=list(lineage or []),
            validation_status=validation_status,
            retention=dict(retention or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CompletionContract:
    required_artifacts: list[str] = field(default_factory=list)
    required_gates: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "CompletionContract":
        value = value or {}
        if not isinstance(value, dict):
            raise ValueError("completion_contract must be an object")
        parsed = {}
        for name in ("required_artifacts", "required_gates"):
            items = value.get(name, [])
            if not isinstance(items, list) or not all(
                isinstance(item, str) and item.strip() for item in items
            ):
                raise ValueError(f"completion_contract.{name} must be a list of strings")
            parsed[name] = list(items)
        return cls(**parsed)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewPolicy:
    """Bounded executor-reviewer loop attached to one executable task."""

    max_rounds: int = 3
    dimensions: list[str] = field(default_factory=list)
    blocking_severities: list[str] = field(
        default_factory=lambda: ["CRITICAL", "HIGH"]
    )
    require_independent_agent: bool = True
    human_approval_after_pass: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "ReviewPolicy":
        value = value or {}
        if not isinstance(value, dict):
            raise ValueError("review_policy must be an object")
        max_rounds = value.get("max_rounds", 3)
        if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds < 1:
            raise ValueError("review_policy.max_rounds must be a positive integer")
        parsed: dict[str, Any] = {"max_rounds": max_rounds}
        for name in ("dimensions", "blocking_severities"):
            default = ["CRITICAL", "HIGH"] if name == "blocking_severities" else []
            items = value.get(name, default)
            if not isinstance(items, list) or not all(
                isinstance(item, str) and item.strip() for item in items
            ):
                raise ValueError(f"review_policy.{name} must be a list of strings")
            parsed[name] = list(items)
        for name, default in (
            ("require_independent_agent", True),
            ("human_approval_after_pass", False),
        ):
            flag = value.get(name, default)
            if not isinstance(flag, bool):
                raise ValueError(f"review_policy.{name} must be boolean")
            parsed[name] = flag
        return cls(**parsed)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewResult:
    """Structured output returned by an independent reviewer process."""

    verdict: str
    summary: str = ""
    findings: list[dict[str, Any]] = field(default_factory=list)
    criteria: list[dict[str, Any]] = field(default_factory=list)
    reviewer: str = ""
    artifact_refs: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReviewResult":
        if not isinstance(value, dict):
            raise ValueError("review result must be an object")
        verdict = value.get("verdict", "")
        if verdict not in {"PASS", "CHANGES_REQUIRED", "ERROR"}:
            raise ValueError(
                "review result verdict must be PASS, CHANGES_REQUIRED, or ERROR"
            )
        summary = value.get("summary", "")
        reviewer = value.get("reviewer", "")
        if not isinstance(summary, str) or not isinstance(reviewer, str):
            raise ValueError("review result summary and reviewer must be strings")
        parsed = {"verdict": verdict, "summary": summary, "reviewer": reviewer}
        for name in ("findings", "criteria"):
            items = value.get(name, [])
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                raise ValueError(f"review result {name} must be a list of objects")
            parsed[name] = list(items)
        artifact_refs = value.get("artifact_refs", [])
        if not isinstance(artifact_refs, list) or not all(
            isinstance(item, str) and item.strip() for item in artifact_refs
        ):
            raise ValueError("review result artifact_refs must be a list of strings")
        parsed["artifact_refs"] = list(artifact_refs)
        return cls(**parsed)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VerifierEvidence:
    gate: str
    verdict: str
    verifier: str
    observed_at: str
    details: dict[str, Any] = field(default_factory=dict)
    artifact_refs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.verdict not in {"PASS", "FAIL", "ERROR"}:
            raise ValueError("verdict must be PASS, FAIL, or ERROR")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvaluatorLoopPolicy:
    """Bounded evaluator/optimizer policy attached to a task.

    ``max_iterations`` applies to each strategy in ``fallback_chain``.  A
    plateau or an exhausted strategy advances to the next strategy; exhausting
    the final strategy produces a durable human escalation.
    """

    max_iterations: int = 3
    plateau_window: int = 3
    plateau_delta: float = 0.0
    fallback_chain: list[str] = field(default_factory=lambda: ["primary"])
    escalation_target: str = "human"

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "EvaluatorLoopPolicy":
        value = value or {}
        if not isinstance(value, dict):
            raise ValueError("evaluator_policy must be an object")

        max_iterations = value.get("max_iterations", 3)
        plateau_window = value.get("plateau_window", 3)
        plateau_delta = value.get("plateau_delta", 0.0)
        fallback_chain = value.get("fallback_chain", ["primary"])
        escalation_target = value.get("escalation_target", "human")

        if (isinstance(max_iterations, bool)
                or not isinstance(max_iterations, int) or max_iterations < 1):
            raise ValueError("evaluator_policy.max_iterations must be a positive integer")
        if (isinstance(plateau_window, bool)
                or not isinstance(plateau_window, int) or plateau_window < 2):
            raise ValueError("evaluator_policy.plateau_window must be at least 2")
        if (isinstance(plateau_delta, bool)
                or not isinstance(plateau_delta, (int, float))
                or plateau_delta < 0):
            raise ValueError("evaluator_policy.plateau_delta must be non-negative")
        if (not isinstance(fallback_chain, list) or not fallback_chain
                or not all(isinstance(item, str) and item.strip()
                           for item in fallback_chain)):
            raise ValueError("evaluator_policy.fallback_chain must be a non-empty list of strings")
        if len(set(fallback_chain)) != len(fallback_chain):
            raise ValueError("evaluator_policy.fallback_chain must not contain duplicates")
        if not isinstance(escalation_target, str) or not escalation_target.strip():
            raise ValueError("evaluator_policy.escalation_target must be a non-empty string")

        return cls(
            max_iterations=max_iterations,
            plateau_window=plateau_window,
            plateau_delta=float(plateau_delta),
            fallback_chain=list(fallback_chain),
            escalation_target=escalation_target,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CheckpointManifest:
    schema_version: str
    checkpoint_version: int
    producer_attempt_id: str
    producer_lease_epoch: int
    checksum: str
    created_at: str
    cursor: str
    artifact_refs: list[str] = field(default_factory=list)

    @classmethod
    def create(
        cls, *, checkpoint_version: int, payload: str, attempt_id: str,
        lease_epoch: int, created_at: str, cursor: str,
        artifact_refs: list[str] | None = None,
    ) -> "CheckpointManifest":
        if checkpoint_version < 1:
            raise ValueError("checkpoint_version must be positive")
        if not attempt_id or not cursor:
            raise ValueError("checkpoint attempt_id and cursor are required")
        return cls(
            schema_version="1.0",
            checkpoint_version=checkpoint_version,
            producer_attempt_id=attempt_id,
            producer_lease_epoch=int(lease_epoch),
            checksum="sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            created_at=created_at,
            cursor=cursor,
            artifact_refs=list(artifact_refs or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


FAILURE_TYPES = frozenset({
    "HARD", "SILENT", "PARTIAL", "CONTRADICTION", "CASCADE", "LOOP", "CONTEXT",
})


@dataclass(frozen=True)
class FailureEnvelope:
    schema_version: str
    failure_id: str
    failure_type: str
    severity: str
    retryable: bool
    message: str
    observed_at: str
    task_name: str
    producer_attempt_id: str
    producer_lease_epoch: int
    evidence: dict[str, Any] = field(default_factory=dict)
    caused_by: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.failure_type not in FAILURE_TYPES:
            raise ValueError("invalid failure_type")
        if self.severity not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
            raise ValueError("invalid failure severity")
        if not self.failure_id or not self.message or not self.task_name:
            raise ValueError("failure_id, message, and task_name are required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
