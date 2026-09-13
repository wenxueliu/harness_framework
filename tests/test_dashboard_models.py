from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from harness_framework.event_journal import EventEnvelope, EventJournal
from harness_framework.local_store import LocalStore
from harness_framework.workspace_models import (
    AttemptWorkspaceBinding,
    ProjectGroup,
    ProjectGroupStatus,
    ProjectWorkspace,
    RunWorkspace,
    WorkspaceAccess,
    WorkspaceBindingType,
    WorkspaceSourceType,
    WorkspaceStatus,
    WorkspaceStrategy,
)


def test_project_group_can_only_archive():
    group = ProjectGroup(
        "g1", "Core", "", ProjectGroupStatus.ACTIVE, None, {}, "user:alice",
        "2026-09-13T00:00:00Z", "2026-09-13T00:00:00Z",
    )
    archived = group.archive(updated_at="2026-09-14T00:00:00Z")
    assert archived.status is ProjectGroupStatus.ARCHIVED
    assert archived.revision == 2
    assert archived.archive(updated_at="later") is archived


def test_git_clone_project_workspace_requires_url():
    with pytest.raises(ValueError, match="git_url"):
        ProjectWorkspace(
            "p1", "g1", "repo", WorkspaceSourceType.GIT_CLONE, "root:repo",
            None, "main", WorkspaceAccess.READ_WRITE, WorkspaceStatus.READY, {},
        )


def test_run_workspace_enforces_state_machine_and_demo_marker():
    workspace = RunWorkspace(
        "rw1", "req1", "run1", "p1", WorkspaceStrategy.GIT_WORKTREE,
        "abc", WorkspaceStatus.PROVISIONING, True, False, None, None,
        root_ref="runs:run1",
    )
    ready = workspace.transition(WorkspaceStatus.READY)
    active = ready.transition(WorkspaceStatus.ACTIVE)
    assert active.revision == 3
    with pytest.raises(ValueError, match="invalid workspace transition"):
        active.transition(WorkspaceStatus.DELETED)
    with pytest.raises(ValueError, match="temporary_demo"):
        RunWorkspace(
            "rw2", "req1", "run2", None, WorkspaceStrategy.DEMO_TEMP,
            None, WorkspaceStatus.PROVISIONING, True, False, None, None,
        )


def test_attempt_binding_is_immutable_and_serializes_scope():
    binding = AttemptWorkspaceBinding(
        "b1", "req1", "run1", "task1", "attempt1", "rw1",
        WorkspaceBindingType.RUN_SHARED, ("src/**",), "abc", True,
        "2026-09-13T00:00:00Z",
    )
    assert binding.to_dict()["binding_type"] == "RUN_SHARED"
    assert binding.to_dict()["write_scope"] == ["src/**"]
    with pytest.raises(FrozenInstanceError):
        binding.workspace_id = "rw2"  # type: ignore[misc]


def test_event_envelope_requires_monotonic_shape_and_timezone():
    event = EventEnvelope(
        "e1", 1, "TASK_STATUS_CHANGED", "2026-09-13T00:00:00Z",
        {"req_id": "req1"}, {"type": "human", "id": "user:alice"}, {},
    )
    assert event.to_dict()["sequence"] == 1
    with pytest.raises(ValueError, match="timezone"):
        EventEnvelope(
            "e2", 2, "TYPE", "2026-09-13T00:00:00",
            {"req_id": "req1"}, {"type": "system", "id": "daemon"}, {},
        )


def test_event_journal_appends_and_replays_after_event_id():
    journal = EventJournal(LocalStore())
    first = journal.append(
        "RUN_CREATED", subject={"req_id": "req1"},
        actor={"type": "human", "id": "user:alice"},
    )
    second = journal.append(
        "TASK_STATUS_CHANGED", subject={"req_id": "req1", "task_id": "build"},
        actor={"type": "agent", "id": "agent1"},
    )
    events, reset = journal.replay(after_event_id=first.event_id)
    assert reset is False
    assert [event.event_id for event in events] == [second.event_id]
    assert second.sequence == first.sequence + 1
    assert journal.replay(after_event_id="missing") == ([], True)
