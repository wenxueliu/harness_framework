from __future__ import annotations

import pytest

from harness_framework.contracts import (
    AgentContract, ArtifactManifest, CompletionContract, VerifierEvidence,
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
