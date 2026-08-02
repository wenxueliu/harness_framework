from __future__ import annotations

import pytest

from harness_framework.changesets import ChangeSetStore
from tests.conftest import MockConsulStore


def test_complete_changeset_lifecycle_preserves_analysis():
    changesets = ChangeSetStore(MockConsulStore())
    change = changesets.propose(
        "req-1", changes={"requirement": {"title": "new"}},
        base_versions={"requirement": "v1-a"}, actor="alice",
    )
    analyzed = changesets.transition(
        "req-1", change.change_id, "IMPACT_ANALYZED", actor="analyst",
        impact_analysis={"affected_tasks": ["build", "test"]},
    )
    approved = changesets.transition(
        "req-1", change.change_id, "APPROVED", actor="owner"
    )
    applied = changesets.transition(
        "req-1", change.change_id, "APPLIED", actor="deployer"
    )

    assert analyzed.impact_analysis["affected_tasks"] == ["build", "test"]
    assert approved.status == "APPROVED"
    assert applied.status == "APPLIED"
    assert applied.decided_by == "deployer"


@pytest.mark.parametrize("terminal", ["REJECTED", "SUPERSEDED"])
def test_changeset_can_terminate_without_application(terminal):
    changesets = ChangeSetStore(MockConsulStore())
    change = changesets.propose(
        "req-1", changes={"plan": {"steps": []}}, base_versions={}, actor="alice"
    )
    result = changesets.transition(
        "req-1", change.change_id, terminal, actor="owner", reason="obsolete"
    )
    assert result.status == terminal
    assert result.reason == "obsolete"


def test_approval_requires_impact_analysis_and_cannot_skip_state():
    changesets = ChangeSetStore(MockConsulStore())
    change = changesets.propose(
        "req-1", changes={"dag": {}}, base_versions={}, actor="alice"
    )
    with pytest.raises(ValueError, match="invalid changeset transition"):
        changesets.transition("req-1", change.change_id, "APPROVED", actor="owner")
    with pytest.raises(ValueError, match="impact_analysis is required"):
        changesets.transition(
            "req-1", change.change_id, "IMPACT_ANALYZED", actor="analyst"
        )


def test_terminal_changeset_cannot_be_reopened():
    changesets = ChangeSetStore(MockConsulStore())
    change = changesets.propose(
        "req-1", changes={"plan": {}}, base_versions={}, actor="alice"
    )
    changesets.transition("req-1", change.change_id, "REJECTED", actor="owner")
    with pytest.raises(ValueError, match="REJECTED -> IMPACT_ANALYZED"):
        changesets.transition(
            "req-1", change.change_id, "IMPACT_ANALYZED", actor="analyst",
            impact_analysis={"affected_tasks": []},
        )
