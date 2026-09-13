from __future__ import annotations

import os
import subprocess

import pytest

from harness_framework.api_errors import ConflictError, ValidationError
from harness_framework.local_store import LocalStore
from harness_framework.project_groups import ProjectGroupService
from harness_framework.workspace_manager import WorkspaceManager
from harness_framework.workspace_security import WorkspaceSecurity


@pytest.fixture
def manager(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    repo = allowed / "repo"
    repo.mkdir()
    runs = tmp_path / "managed"
    runs.mkdir()
    demo = tmp_path / "demo"
    demo.mkdir()
    store = LocalStore()
    groups = ProjectGroupService(store)
    group = groups.create(name="Core", description="", actor="local:alice")
    return (
        WorkspaceManager(
            store, WorkspaceSecurity({
                "projects": str(allowed), "runs": str(runs), "demo": str(demo),
            }), groups, provision_root_alias="runs", demo_mode=True,
            demo_root_alias="demo",
        ),
        group, repo,
    )


def test_register_local_uses_root_alias_and_preflight(manager):
    service, group, _ = manager
    workspace = service.register_local(
        group_id=group["group_id"], name="Repo", root_alias="projects",
        relative_path="repo",
    )
    assert workspace["root_ref"] == "projects:repo"
    assert service.list_for_group(group["group_id"])[0]["workspace_id"] == workspace["workspace_id"]
    assert service.preflight(workspace["workspace_id"])["readable"] is True


def test_root_alias_rejects_traversal_and_symlink_escape(manager, tmp_path):
    service, group, repo = manager
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValidationError, match="路径"):
        service.register_local(
            group_id=group["group_id"], name="Bad", root_alias="projects",
            relative_path="../outside",
        )
    with pytest.raises(ValidationError, match="越过"):
        service.register_local(
            group_id=group["group_id"], name="Bad", root_alias="projects",
            relative_path="repo/escape",
        )


def test_preflight_reports_git_baseline_and_dirty_state(manager):
    service, group, repo = manager
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "README.md").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    workspace = service.register_local(
        group_id=group["group_id"], name="Repo", root_alias="projects",
        relative_path="repo",
    )
    clean = service.preflight(workspace["workspace_id"])
    assert clean["git"] is True and clean["head_sha"] and clean["dirty"] is False
    (repo / "README.md").write_text("changed", encoding="utf-8")
    assert service.preflight(workspace["workspace_id"])["dirty"] is True


def _init_git(repo):
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "README.md").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)


def test_original_requires_dirty_acceptance_and_captures_snapshot(manager):
    service, group, repo = manager
    _init_git(repo)
    (repo / "README.md").write_text("changed", encoding="utf-8")
    workspace = service.register_local(
        group_id=group["group_id"], name="Repo", root_alias="projects",
        relative_path="repo",
    )
    with pytest.raises(ValidationError, match="未提交"):
        service.provision_run_workspace(
            req_id="req", run_id="run-1", project_workspace_id=workspace["workspace_id"],
            strategy="ORIGINAL",
        )
    provisioned = service.provision_run_workspace(
        req_id="req", run_id="run-1", project_workspace_id=workspace["workspace_id"],
        strategy="ORIGINAL", accept_dirty=True,
    )
    assert provisioned["snapshot_manifest_id"]
    assert "root_ref" not in provisioned


def test_git_worktree_freezes_ref_and_binding_resolves_same_directory(manager):
    service, group, repo = manager
    _init_git(repo)
    workspace = service.register_local(
        group_id=group["group_id"], name="Repo", root_alias="projects",
        relative_path="repo",
    )
    provisioned = service.provision_run_workspace(
        req_id="req", run_id="run-2", project_workspace_id=workspace["workspace_id"],
        strategy="GIT_WORKTREE", git_ref="HEAD",
    )
    assert len(provisioned["resolved_commit_sha"]) == 40
    binding = service.bind_attempt(
        req_id="req", run_id="run-2", task_id="build", attempt_id="attempt-1",
        write_scope=("src/**",),
    )
    assert binding["binding_type"] == "RUN_SHARED"
    assert service.get_run_workspace("req", "run-2")["status"] == "ACTIVE"
    resolved = service.resolve_attempt_path("req", "run-2", "build", "attempt-1")
    assert os.path.isfile(os.path.join(resolved, "README.md"))
    with pytest.raises(ConflictError):
        service.bind_attempt(
            req_id="req", run_id="run-2", task_id="build", attempt_id="attempt-1"
        )


def test_controlled_copy_and_explicit_demo_workspace(manager):
    service, group, repo = manager
    (repo / "plain.txt").write_text("plain", encoding="utf-8")
    workspace = service.register_local(
        group_id=group["group_id"], name="Repo", root_alias="projects",
        relative_path="repo",
    )
    copied = service.provision_run_workspace(
        req_id="req", run_id="run-copy", project_workspace_id=workspace["workspace_id"],
        strategy="CONTROLLED_COPY",
    )
    copied_internal = service.get_run_workspace("req", "run-copy", include_root=True)
    assert copied["strategy"] == "CONTROLLED_COPY"
    assert os.path.isfile(service.security.resolve_child(copied_internal["root_ref"], "plain.txt"))

    demo = service.provision_run_workspace(
        req_id="req", run_id="run-demo", project_workspace_id=None,
        strategy="DEMO_TEMP",
    )
    assert demo["temporary_demo"] is True


def test_demo_workspace_rejected_unless_explicitly_enabled(manager):
    service, _, _ = manager
    disabled = WorkspaceManager(
        service.store, service.security, service.project_groups,
        provision_root_alias="runs", demo_mode=False, demo_root_alias="demo",
    )
    with pytest.raises(ValidationError, match="未启用"):
        disabled.provision_run_workspace(
            req_id="req", run_id="run-demo", project_workspace_id=None,
            strategy="DEMO_TEMP",
        )


def test_finishing_original_releases_write_lock_and_retains_changes(manager):
    service, group, repo = manager
    _init_git(repo)
    workspace = service.register_local(
        group_id=group["group_id"], name="Repo", root_alias="projects",
        relative_path="repo",
    )
    service.provision_run_workspace(
        req_id="req", run_id="run-finish",
        project_workspace_id=workspace["workspace_id"], strategy="ORIGINAL",
    )
    (repo / "README.md").write_text("human change", encoding="utf-8")
    finished = service.finish_run_workspace("req", "run-finish")
    assert finished["status"] == "RETAINED"
    lock, _ = service.store.kv_get(
        f"workspaces/projects/{workspace['workspace_id']}/original-write-lock"
    )
    assert lock is None


def test_retained_workspace_moves_to_trash_and_can_be_restored(manager):
    service, group, repo = manager
    (repo / "result.txt").write_text("keep", encoding="utf-8")
    project = service.register_local(
        group_id=group["group_id"], name="Repo", root_alias="projects",
        relative_path="repo",
    )
    service.provision_run_workspace(
        req_id="req", run_id="run-retained",
        project_workspace_id=project["workspace_id"], strategy="CONTROLLED_COPY",
    )
    assert service.finish_run_workspace("req", "run-retained")["status"] == "RETAINED"
    service.request_cleanup("req", "run-retained")
    trashed = service.trash_run_workspace("req", "run-retained")
    assert trashed["status"] == "TRASHED"
    restored = service.restore_run_workspace("req", "run-retained")
    assert restored["status"] == "READY"
    restored_internal = service.get_run_workspace("req", "run-retained", include_root=True)
    assert os.path.isfile(service.security.resolve_child(restored_internal["root_ref"], "result.txt"))


def test_empty_demo_workspace_requires_grace_period_before_purge(manager):
    service, _, _ = manager
    service.provision_run_workspace(
        req_id="req", run_id="run-trash", project_workspace_id=None,
        strategy="DEMO_TEMP",
    )
    assert service.finish_run_workspace("req", "run-trash")["status"] == "CLEANUP_PENDING"
    service.trash_run_workspace("req", "run-trash")
    with pytest.raises(ConflictError) as error:
        service.purge_run_workspace("req", "run-trash")
    assert error.value.code == "WORKSPACE_TRASH_GRACE_PERIOD"
    assert service.purge_run_workspace("req", "run-trash", force=True)["status"] == "DELETED"


def test_git_clone_workspace_requires_allowlisted_https_host(manager):
    service, group, _ = manager
    with pytest.raises(ValidationError) as error:
        service.register_git(
            group_id=group["group_id"], name="Remote", git_url="http://github.com/acme/repo.git",
        )
    assert error.value.code == "GIT_URL_FORBIDDEN"
    with pytest.raises(ValidationError) as error:
        service.register_git(
            group_id=group["group_id"], name="Remote", git_url="https://evil.example/repo.git",
        )
    assert error.value.code == "GIT_HOST_NOT_ALLOWED"


def test_isolated_attempt_binding_resolves_its_copy(manager):
    service, group, repo = manager
    (repo / "source.txt").write_text("source", encoding="utf-8")
    project = service.register_local(
        group_id=group["group_id"], name="Repo", root_alias="projects",
        relative_path="repo",
    )
    service.provision_run_workspace(
        req_id="req", run_id="run-isolated",
        project_workspace_id=project["workspace_id"], strategy="CONTROLLED_COPY",
    )
    isolated = service.provision_isolated_workspace(
        req_id="req", run_id="run-isolated", task_id="task", attempt_id="a1"
    )
    binding = service.bind_attempt(
        req_id="req", run_id="run-isolated", task_id="task", attempt_id="a1",
        isolated_workspace_id=isolated["workspace_id"],
    )
    assert binding["workspace_id"] == isolated["workspace_id"]
    isolated_root = service.resolve_attempt_path("req", "run-isolated", "task", "a1")
    assert os.path.isfile(os.path.join(isolated_root, "source.txt"))
    (repo / "source.txt").write_text("changed source", encoding="utf-8")
    assert open(os.path.join(isolated_root, "source.txt"), encoding="utf-8").read() == "source"


def test_shared_workspace_rejects_overlapping_active_write_scopes(manager):
    service, group, repo = manager
    project = service.register_local(
        group_id=group["group_id"], name="Repo", root_alias="projects",
        relative_path="repo",
    )
    service.provision_run_workspace(
        req_id="req", run_id="run-scope",
        project_workspace_id=project["workspace_id"], strategy="CONTROLLED_COPY",
    )
    service.bind_attempt(
        req_id="req", run_id="run-scope", task_id="one", attempt_id="a1",
        write_scope=("src/**",),
    )
    service.store.kv_put("workflows/req/tasks/one/status", "IN_PROGRESS")
    with pytest.raises(ConflictError) as error:
        service.bind_attempt(
            req_id="req", run_id="run-scope", task_id="two", attempt_id="a2",
            write_scope=("src/main.py",),
        )
    assert error.value.code == "WRITE_SCOPE_CONFLICT"
