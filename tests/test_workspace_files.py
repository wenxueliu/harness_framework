from __future__ import annotations

import hashlib
import os
import subprocess

import pytest

from harness_framework.api_errors import ConflictError, ValidationError
from harness_framework.local_store import LocalStore
from harness_framework.project_groups import ProjectGroupService
from harness_framework.workspace_files import WorkspaceFileService
from harness_framework.workspace_manager import WorkspaceManager
from harness_framework.workspace_security import WorkspaceSecurity


@pytest.fixture
def files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("print('one')\n", encoding="utf-8")
    (repo / ".env").write_text("TOKEN=secret", encoding="utf-8")
    store = LocalStore()
    groups = ProjectGroupService(store)
    group = groups.create(name="Core", description="", actor="local:alice")
    manager = WorkspaceManager(
        store, WorkspaceSecurity({"projects": str(tmp_path)}), groups,
        provision_root_alias="projects",
    )
    project = manager.register_local(
        group_id=group["group_id"], name="Repo", root_alias="projects",
        relative_path="repo",
    )
    workspace = manager.provision_run_workspace(
        req_id="req", run_id="run", project_workspace_id=project["workspace_id"],
        strategy="ORIGINAL",
    )
    binding = manager.bind_attempt(
        req_id="req", run_id="run", task_id="build", attempt_id="attempt"
    )
    return WorkspaceFileService(store, manager), repo, workspace, binding


def _context(workspace):
    return {
        "workspace_id": workspace["run_workspace_id"], "req_id": "req",
        "run_id": "run", "task_id": "build", "attempt_id": "attempt",
    }


def test_tree_hides_sensitive_files_and_reads_utf8(files):
    service, _, workspace, binding = files
    tree = service.tree(**_context(workspace))
    assert [entry["name"] for entry in tree["entries"]] == ["src"]
    content = service.read_file(**_context(workspace), path="src/main.py")
    assert content["content"] == "print('one')\n"
    assert content["binding_id"] == binding["binding_id"]
    assert content["writable"] is True
    with pytest.raises(ValidationError, match="敏感"):
        service.read_file(**_context(workspace), path=".env")


def test_file_write_uses_hash_idempotency_and_detects_conflict(files):
    service, repo, workspace, _ = files
    context = _context(workspace)
    base = hashlib.sha256(b"print('one')\n").hexdigest()
    saved = service.write_file(
        **context, path="src/main.py", content="print('two')\n",
        expected_sha256=base, idempotency_key="save-one", actor="local:alice",
        reason="fix",
    )
    replay = service.write_file(
        **context, path="src/main.py", content="print('two')\n",
        expected_sha256=base, idempotency_key="save-one", actor="local:alice",
        reason="fix",
    )
    assert replay == saved
    assert (repo / "src" / "main.py").read_text() == "print('two')\n"
    with pytest.raises(ConflictError) as error:
        service.write_file(
            **context, path="src/main.py", content="stale\n",
            expected_sha256=base, idempotency_key="save-two", actor="local:alice",
            reason="stale",
        )
    assert error.value.code == "FILE_VERSION_CONFLICT"


def test_new_file_requires_null_hash_and_existing_parent(files):
    service, repo, workspace, _ = files
    saved = service.write_file(
        **_context(workspace), path="src/new.py", content="pass\n",
        expected_sha256=None, idempotency_key="new-one", actor="local:alice",
        reason="new file",
    )
    assert saved["sha256"]
    assert (repo / "src" / "new.py").exists()
    with pytest.raises(ValidationError, match="父目录"):
        service.write_file(
            **_context(workspace), path="missing/new.py", content="pass\n",
            expected_sha256=None, idempotency_key="new-two", actor="local:alice",
            reason="new file",
        )


def test_binary_large_and_symlink_files_are_safe(files):
    service, repo, workspace, _ = files
    (repo / "src" / "binary.bin").write_bytes(b"\x00\x01")
    binary = service.read_file(**_context(workspace), path="src/binary.bin")
    assert binary["binary"] is True and binary["content"] is None
    (repo / "src" / "link").symlink_to(repo / "src" / "main.py")
    with pytest.raises(ValidationError, match="符号链接"):
        service.read_file(**_context(workspace), path="src/link")


def test_workspace_id_must_match_attempt_binding(files):
    service, _, _, _ = files
    with pytest.raises(ValidationError, match="不匹配"):
        service.tree(
            workspace_id="another", req_id="req", run_id="run",
            task_id="build", attempt_id="attempt",
        )


def test_write_scope_is_enforced(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / "src").mkdir(); (repo / "docs").mkdir()
    (repo / "src" / "ok.py").write_text("one\n")
    (repo / "docs" / "no.md").write_text("one\n")
    store = LocalStore(); groups = ProjectGroupService(store)
    group = groups.create(name="Core", description="", actor="local:alice")
    manager = WorkspaceManager(store, WorkspaceSecurity({"root": str(tmp_path)}), groups)
    project = manager.register_local(group_id=group["group_id"], name="Repo", root_alias="root", relative_path="repo")
    workspace = manager.provision_run_workspace(req_id="req", run_id="run", project_workspace_id=project["workspace_id"], strategy="ORIGINAL")
    manager.bind_attempt(req_id="req", run_id="run", task_id="task", attempt_id="attempt", write_scope=("src/**",))
    service = WorkspaceFileService(store, manager)
    context = _context(workspace); context["task_id"] = "task"
    service.write_file(**context, path="src/ok.py", content="two\n",
                       expected_sha256=hashlib.sha256(b"one\n").hexdigest(),
                       idempotency_key="ok", actor="local:alice", reason="test")
    with pytest.raises(ValidationError) as error:
        service.write_file(**context, path="docs/no.md", content="two\n",
                           expected_sha256=hashlib.sha256(b"one\n").hexdigest(),
                           idempotency_key="no", actor="local:alice", reason="test")
    assert error.value.code == "WRITE_SCOPE_FORBIDDEN"


def test_registered_actions_reject_arbitrary_commands(files):
    service, _, workspace, _ = files
    actions = service.list_actions(**_context(workspace))
    assert {item["action_id"] for item in actions} == {
        "git-diff-check", "python-syntax-check"
    }
    result = service.run_action(
        **_context(workspace), action_id="python-syntax-check",
        actor="local:alice",
    )
    assert result["exit_code"] == 0
    assert "checked 1 Python files" in result["stdout"]
    with pytest.raises(ValidationError) as error:
        service.run_action(
            **_context(workspace), action_id="shell", actor="local:alice"
        )
    assert error.value.code == "ACTION_NOT_REGISTERED"


def test_checkpoint_creates_one_idempotent_git_commit(files):
    service, repo, workspace, _ = files
    (repo / ".env").unlink()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "src/main.py"], cwd=repo, check=True)
    subprocess.run([
        "git", "-c", "user.name=test", "-c", "user.email=test@example.com",
        "commit", "-q", "-m", "initial",
    ], cwd=repo, check=True)
    (repo / "src" / "main.py").write_text("print('checkpoint')\n", encoding="utf-8")
    first = service.create_checkpoint(
        **_context(workspace), actor="local:alice", message="save progress",
        idempotency_key="checkpoint-one",
    )
    replay = service.create_checkpoint(
        **_context(workspace), actor="local:alice", message="save progress",
        idempotency_key="checkpoint-one",
    )
    assert replay == first
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
        text=True, check=True,
    ).stdout.strip() == first["commit_sha"]
