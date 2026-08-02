from __future__ import annotations

import pytest

from harness_framework.contracts import (
    AgentContract, ArtifactManifest, CheckpointManifest, CompletionContract,
    EvaluatorLoopPolicy, FailureEnvelope, ReviewPolicy, ReviewResult,
    VerifierEvidence,
)


def test_agent_contract_round_trip():
    value = {
        "inputs": ["spec"], "outputs": ["code"],
        "responsibilities": ["tests"], "exclusions": ["deploy"],
        "permissions": ["repo:write"], "context_budget": 8000,
    }
    assert AgentContract.from_dict(value).to_dict() == value


@pytest.mark.parametrize("field", [
    "inputs", "outputs", "responsibilities", "exclusions", "permissions",
])
def test_agent_contract_rejects_non_string_lists(field):
    with pytest.raises(ValueError):
        AgentContract.from_dict({field: [1]})


def test_agent_contract_defaults_are_safe():
    contract = AgentContract.from_dict(None)
    assert contract.context_budget == 0
    assert contract.permissions == []


def test_artifact_manifest_contains_integrity_and_producer_metadata():
    manifest = ArtifactManifest.create(
        artifact_version=2, key="report", value="hello",
        attempt_id="attempt-1", lease_epoch=3,
        created_at="2026-01-01T00:00:00Z", lineage=["upstream/v1"],
        validation_status="VALID", retention={"class": "release"},
    )
    value = manifest.to_dict()
    assert value["schema_version"] == "1.0"
    assert value["checksum"].startswith("sha256:")
    assert value["producer_attempt_id"] == "attempt-1"
    assert value["lineage"] == ["upstream/v1"]


def test_completion_contract_and_verifier_evidence():
    contract = CompletionContract.from_dict({
        "required_artifacts": ["report"], "required_gates": ["tests"],
    })
    assert contract.required_gates == ["tests"]
    evidence = VerifierEvidence(
        gate="tests", verdict="PASS", verifier="agent-1",
        observed_at="2026-01-01T00:00:00Z", details={"passed": 10},
    )
    assert evidence.to_dict()["verdict"] == "PASS"


def test_review_policy_and_result_round_trip():
    policy = ReviewPolicy.from_dict({
        "max_rounds": 2,
        "dimensions": ["correctness"],
        "blocking_severities": ["HIGH"],
        "require_independent_agent": True,
        "human_approval_after_pass": True,
    })
    assert policy.max_rounds == 2
    assert policy.human_approval_after_pass is True
    result = ReviewResult.from_dict({
        "verdict": "CHANGES_REQUIRED",
        "summary": "fix race",
        "reviewer": "reviewer-1",
        "findings": [{"id": "R-1"}],
    })
    assert result.findings == [{"id": "R-1"}]


@pytest.mark.parametrize("value", [
    {"max_rounds": 0},
    {"dimensions": "correctness"},
    {"require_independent_agent": "yes"},
])
def test_review_policy_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        ReviewPolicy.from_dict(value)


def test_review_result_rejects_unknown_verdict():
    with pytest.raises(ValueError):
        ReviewResult.from_dict({"verdict": "MAYBE"})


def test_evaluator_loop_policy_round_trip_and_defaults():
    policy = EvaluatorLoopPolicy.from_dict({
        "max_iterations": 4,
        "plateau_window": 2,
        "plateau_delta": 0.25,
        "fallback_chain": ["primary", "narrowed", "degraded"],
        "escalation_target": "release-manager",
    })
    assert policy.to_dict() == {
        "max_iterations": 4,
        "plateau_window": 2,
        "plateau_delta": 0.25,
        "fallback_chain": ["primary", "narrowed", "degraded"],
        "escalation_target": "release-manager",
    }
    assert EvaluatorLoopPolicy.from_dict(None).fallback_chain == ["primary"]


@pytest.mark.parametrize("value", [
    {"max_iterations": 0},
    {"plateau_window": 1},
    {"plateau_delta": -0.1},
    {"fallback_chain": []},
    {"fallback_chain": ["primary", "primary"]},
    {"escalation_target": ""},
])
def test_evaluator_loop_policy_rejects_unsafe_values(value):
    with pytest.raises(ValueError):
        EvaluatorLoopPolicy.from_dict(value)


def test_checkpoint_manifest_contains_resume_integrity_and_owner():
    manifest = CheckpointManifest.create(
        checkpoint_version=2, payload='{"offset":10}',
        attempt_id="attempt-1", lease_epoch=4,
        created_at="2026-01-01T00:00:00Z", cursor="batch:10",
        artifact_refs=["artifacts/output/v1"],
    ).to_dict()
    assert manifest["checkpoint_version"] == 2
    assert manifest["producer_attempt_id"] == "attempt-1"
    assert manifest["checksum"].startswith("sha256:")
    assert manifest["cursor"] == "batch:10"


@pytest.mark.parametrize("failure_type", [
    "HARD", "SILENT", "PARTIAL", "CONTRADICTION", "CASCADE", "LOOP", "CONTEXT",
])
def test_failure_envelope_supports_production_failure_taxonomy(failure_type):
    envelope = FailureEnvelope(
        schema_version="1.0", failure_id="failure-1", failure_type=failure_type,
        severity="HIGH", retryable=True, message="failed",
        observed_at="2026-01-01T00:00:00Z", task_name="api",
        producer_attempt_id="attempt-1", producer_lease_epoch=2,
        evidence={"log": "ref"}, caused_by=["failure-upstream"],
    )
    assert envelope.to_dict()["failure_type"] == failure_type
