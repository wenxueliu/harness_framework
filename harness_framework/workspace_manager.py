"""Project Workspace registry and filesystem preflight service."""
from __future__ import annotations

import json
import datetime
import fnmatch
import hashlib
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import replace
from typing import Any, Optional
from urllib.parse import urlparse

from .api_errors import ConflictError, NotFoundError, ValidationError
from .kv_store_protocol import KVStore
from .project_groups import ProjectGroupService
from .workspace_models import (
    ProjectWorkspace,
    AttemptWorkspaceBinding,
    RunWorkspace,
    WorkspaceAccess,
    WorkspaceBindingType,
    WorkspaceSourceType,
    WorkspaceStrategy,
    WorkspaceStatus,
)
from .workspace_security import WorkspaceSecurity


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _decode(raw: str) -> ProjectWorkspace:
    data = json.loads(raw)
    return ProjectWorkspace(
        workspace_id=data["workspace_id"], group_id=data["group_id"],
        name=data["name"], source_type=WorkspaceSourceType(data["source_type"]),
        root_ref=data["root_ref"], git_url=data.get("git_url"),
        default_ref=data.get("default_ref"), access=WorkspaceAccess(data["access"]),
        status=WorkspaceStatus(data["status"]), policy=dict(data.get("policy", {})),
        revision=int(data.get("revision", 1)),
    )


def _decode_run_workspace(raw: str) -> RunWorkspace:
    data = json.loads(raw)
    return RunWorkspace(
        run_workspace_id=data["run_workspace_id"], req_id=data["req_id"],
        run_id=data["run_id"], project_workspace_id=data.get("project_workspace_id"),
        strategy=WorkspaceStrategy(data["strategy"]),
        resolved_commit_sha=data.get("resolved_commit_sha"),
        status=WorkspaceStatus(data["status"]), read_write=bool(data["read_write"]),
        temporary_demo=bool(data.get("temporary_demo", False)),
        retention_until=data.get("retention_until"),
        snapshot_manifest_id=data.get("snapshot_manifest_id"),
        revision=int(data.get("revision", 1)), root_ref=data.get("root_ref"),
    )


class WorkspaceManager:
    def __init__(
        self, store: KVStore, security: WorkspaceSecurity,
        project_groups: Optional[ProjectGroupService] = None,
        *, provision_root_alias: str = "default", demo_mode: bool = False,
        demo_root_alias: str = "demo", retention_days: int = 7,
        allowed_git_hosts: tuple[str, ...] = (),
    ):
        self.store = store
        self.security = security
        self.project_groups = project_groups or ProjectGroupService(store)
        self.provision_root_alias = provision_root_alias
        self.demo_mode = demo_mode
        self.demo_root_alias = demo_root_alias
        self.retention_days = retention_days
        self.allowed_git_hosts = frozenset(host.casefold() for host in allowed_git_hosts)

    def register_local(
        self, *, group_id: str, name: str, root_alias: str,
        relative_path: str, access: str = "READ_WRITE",
        policy: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        self.project_groups._require_active(group_id)
        if not name.strip():
            raise ValidationError("Workspace 名称不能为空", code="WORKSPACE_NAME_REQUIRED")
        try:
            access_value = WorkspaceAccess(access)
        except ValueError as exc:
            raise ValidationError("无效的 Workspace access") from exc
        root_ref = self.security.make_root_ref(root_alias, relative_path)
        self.security.resolve_root_ref(root_ref)
        workspace = ProjectWorkspace(
            workspace_id=f"pws_{uuid.uuid4().hex}", group_id=group_id,
            name=name.strip(), source_type=WorkspaceSourceType.LOCAL_PATH,
            root_ref=root_ref, git_url=None, default_ref=None,
            access=access_value, status=WorkspaceStatus.READY,
            policy=dict(policy or {}), revision=1,
        )
        self._create(workspace)
        return workspace.to_dict()

    def register_git(
        self, *, group_id: str, name: str, git_url: str,
        default_ref: str = "main", access: str = "READ_WRITE",
        policy: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Clone an allow-listed HTTPS repository into the managed root."""
        self.project_groups._require_active(group_id)
        if not name.strip():
            raise ValidationError("Workspace 名称不能为空", code="WORKSPACE_NAME_REQUIRED")
        try:
            access_value = WorkspaceAccess(access)
        except ValueError as exc:
            raise ValidationError("无效的 Workspace access") from exc
        parsed = urlparse(git_url)
        if (parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or git_url.startswith("-")):
            raise ValidationError(
                "Git URL 必须是不含内嵌凭据的 HTTPS URL", code="GIT_URL_FORBIDDEN"
            )
        if parsed.hostname.casefold() not in self.allowed_git_hosts:
            raise ValidationError(
                "Git host 未加入允许列表", code="GIT_HOST_NOT_ALLOWED",
                details={"host": parsed.hostname},
            )
        if not default_ref.strip() or default_ref.startswith("-"):
            raise ValidationError("Git default_ref 无效", code="GIT_REF_INVALID")
        workspace_id = f"pws_{uuid.uuid4().hex}"
        root_ref = self.security.make_root_ref(
            self.provision_root_alias, f"projects/{workspace_id}"
        )
        target = self.security.resolve_root_ref(root_ref, must_exist=False)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.lexists(target):
            raise ConflictError("Workspace clone 目录已存在", code="WORKSPACE_EXISTS")
        process = subprocess.run(
            ["git", "clone", "--branch", default_ref, "--single-branch", "--",
             git_url, target], capture_output=True, text=True, timeout=300,
            check=False, env={"PATH": os.environ.get("PATH", ""),
                              "GIT_TERMINAL_PROMPT": "0", "LANG": "C.UTF-8"},
        )
        if process.returncode != 0:
            if os.path.exists(target):
                shutil.rmtree(target)
            raise ValidationError(
                "Git Workspace clone 失败", code="GIT_CLONE_FAILED",
                details={"stderr": process.stderr[-500:]},
            )
        workspace = ProjectWorkspace(
            workspace_id=workspace_id, group_id=group_id, name=name.strip(),
            source_type=WorkspaceSourceType.GIT_CLONE, root_ref=root_ref,
            git_url=git_url, default_ref=default_ref, access=access_value,
            status=WorkspaceStatus.READY, policy=dict(policy or {}), revision=1,
        )
        try:
            self._create(workspace)
        except Exception:
            shutil.rmtree(target)
            raise
        return workspace.to_dict()

    def _create(self, workspace: ProjectWorkspace) -> None:
        key = f"workspaces/projects/{workspace.workspace_id}/record"
        if not self.store.kv_put(key, json.dumps(workspace.to_dict()), cas=0):
            raise ConflictError("Workspace ID 冲突", code="WORKSPACE_ID_CONFLICT")
        self.store.kv_put(
            f"project-groups/{workspace.group_id}/workspaces/{workspace.workspace_id}",
            "registered",
        )

    def get(self, workspace_id: str) -> dict[str, Any]:
        raw, _ = self.store.kv_get(f"workspaces/projects/{workspace_id}/record")
        if not raw:
            raise NotFoundError("Project Workspace 不存在", code="WORKSPACE_NOT_FOUND")
        return _decode(raw).to_dict()

    def update(
        self, workspace_id: str, *, expected_revision: int,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        key = f"workspaces/projects/{workspace_id}/record"
        raw, index = self.store.kv_get(key)
        if not raw:
            raise NotFoundError("Project Workspace 不存在", code="WORKSPACE_NOT_FOUND")
        current = _decode(raw)
        if current.revision != int(expected_revision):
            raise ConflictError("Workspace 已被其他请求修改", code="WORKSPACE_REVISION_CONFLICT",
                                details={"current_revision": current.revision})
        allowed = {"name", "access", "policy", "default_ref"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValidationError("Workspace 包含不支持的字段", code="WORKSPACE_FIELD_INVALID",
                                  details={"fields": sorted(unknown)})
        data = current.to_dict()
        if "name" in changes:
            if not isinstance(changes["name"], str) or not changes["name"].strip():
                raise ValidationError("Workspace 名称不能为空", code="WORKSPACE_NAME_REQUIRED")
            data["name"] = changes["name"].strip()
        if "access" in changes:
            try:
                data["access"] = WorkspaceAccess(str(changes["access"])).value
            except ValueError as exc:
                raise ValidationError("无效的 Workspace access") from exc
        for field in ("policy", "default_ref"):
            if field in changes:
                data[field] = changes[field]
        data["revision"] = current.revision + 1
        updated = _decode(json.dumps(data))
        if not self.store.kv_put(key, json.dumps(updated.to_dict()), cas=index):
            raise ConflictError("Workspace 已被其他请求修改", code="WORKSPACE_REVISION_CONFLICT")
        return updated.to_dict()

    def list_for_group(self, group_id: str) -> list[dict[str, Any]]:
        self.project_groups._require_real_group(group_id)
        indexes, _ = self.store.kv_list(
            f"project-groups/{group_id}/workspaces/", limit=1000
        )
        result = []
        for item in indexes:
            workspace_id = item["key"].rsplit("/", 1)[-1]
            try:
                result.append(self.get(workspace_id))
            except NotFoundError:
                continue
        return sorted(result, key=lambda item: item["name"].casefold())

    def preflight(self, workspace_id: str) -> dict[str, Any]:
        record = _decode(json.dumps(self.get(workspace_id)))
        path = self.security.resolve_root_ref(record.root_ref)
        readable = os.access(path, os.R_OK | os.X_OK)
        writable = os.access(path, os.W_OK) and record.access is WorkspaceAccess.READ_WRITE
        git = os.path.isdir(os.path.join(path, ".git")) or self._git_ok(path, "rev-parse", "--git-dir")
        result: dict[str, Any] = {
            "workspace_id": workspace_id,
            "exists": True,
            "readable": readable,
            "writable": writable,
            "git": git,
        }
        if git:
            result.update({
                "head_sha": self._git(path, "rev-parse", "HEAD", required=False),
                "branch": self._git(path, "branch", "--show-current", required=False),
                "dirty": bool(self._git(path, "status", "--porcelain", required=False)),
            })
        return result

    def provision_run_workspace(
        self, *, req_id: str, run_id: str,
        project_workspace_id: Optional[str], strategy: str,
        git_ref: Optional[str] = None, accept_dirty: bool = False,
    ) -> dict[str, Any]:
        try:
            strategy_value = WorkspaceStrategy(strategy)
        except ValueError as exc:
            raise ValidationError("无效的 Run Workspace strategy") from exc
        if strategy_value is WorkspaceStrategy.DEMO_TEMP:
            return self._provision_demo(req_id, run_id)
        if not project_workspace_id:
            raise ValidationError(
                "请选择本次运行使用的工作区",
                code="WORKSPACE_SELECTION_REQUIRED",
            )
        project = _decode(json.dumps(self.get(project_workspace_id)))
        source_path = self.security.resolve_root_ref(project.root_ref)
        preflight = self.preflight(project_workspace_id)
        if not preflight["readable"]:
            raise ValidationError("Workspace 不可读", code="WORKSPACE_NOT_READABLE")
        read_write = project.access is WorkspaceAccess.READ_WRITE
        root_ref: Optional[str] = None
        resolved_sha: Optional[str] = None
        manifest_id: Optional[str] = None
        created_path: Optional[str] = None
        original_lock = False
        try:
            if strategy_value is WorkspaceStrategy.ORIGINAL:
                if read_write:
                    lock_key = f"workspaces/projects/{project_workspace_id}/original-write-lock"
                    if not self.store.kv_put(lock_key, run_id, cas=0):
                        holder, _ = self.store.kv_get(lock_key)
                        if holder != run_id:
                            raise ConflictError(
                                "原目录已有另一个可写 Run",
                                code="ORIGINAL_WORKSPACE_IN_USE",
                                details={"run_id": holder},
                            )
                    original_lock = True
                if preflight.get("git"):
                    resolved_sha = preflight.get("head_sha") or None
                    if preflight.get("dirty"):
                        if not accept_dirty:
                            raise ValidationError(
                                "原目录包含未提交修改",
                                code="DIRTY_WORKSPACE_REQUIRES_ACCEPTANCE",
                            )
                        manifest_id = self._capture_dirty_snapshot(
                            req_id, run_id, source_path, resolved_sha
                        )
                root_ref = project.root_ref
            elif strategy_value is WorkspaceStrategy.GIT_WORKTREE:
                if not preflight.get("git"):
                    raise ValidationError(
                        "Git worktree 需要 Git Workspace", code="GIT_WORKSPACE_REQUIRED"
                    )
                ref = git_ref or project.default_ref or "HEAD"
                resolved_sha = self._git(
                    source_path, "rev-parse", f"{ref}^{{commit}}", required=True
                )
                root_ref, created_path = self._provision_target(run_id)
                process = subprocess.run(
                    ["git", "-C", source_path, "worktree", "add", "--detach",
                     created_path, resolved_sha],
                    capture_output=True, text=True, timeout=120, check=False,
                )
                if process.returncode != 0:
                    raise ValidationError(
                        "Git worktree 创建失败", code="GIT_WORKTREE_FAILED",
                        details={"stderr": process.stderr[-500:]},
                    )
            elif strategy_value is WorkspaceStrategy.CONTROLLED_COPY:
                root_ref, created_path = self._provision_target(run_id)
                # _provision_target reserves no directory; copytree creates it.
                shutil.copytree(
                    source_path, created_path, symlinks=False,
                    ignore=shutil.ignore_patterns(".git"),
                )
                if preflight.get("git"):
                    subprocess.run(["git", "-C", created_path, "init", "-q"],
                                   check=False, capture_output=True, text=True)
                    subprocess.run(["git", "-C", created_path, "add", "-A"],
                                   check=False, capture_output=True, text=True)
                    subprocess.run([
                        "git", "-C", created_path, "-c", "user.name=harness",
                        "-c", "user.email=harness@localhost", "commit", "-qm",
                        "baseline",
                    ], check=False, capture_output=True, text=True)
                resolved_sha = preflight.get("head_sha") or None

            if manifest_id is None:
                manifest_id = self._capture_workspace_manifest(
                    req_id, run_id,
                    self.security.resolve_root_ref(root_ref), resolved_sha,
                )

            retention = (
                datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(days=self.retention_days)
            ).isoformat().replace("+00:00", "Z")
            record = RunWorkspace(
                run_workspace_id=f"rws_{uuid.uuid4().hex}", req_id=req_id,
                run_id=run_id, project_workspace_id=project_workspace_id,
                strategy=strategy_value, resolved_commit_sha=resolved_sha,
                status=WorkspaceStatus.READY, read_write=read_write,
                temporary_demo=False, retention_until=retention,
                snapshot_manifest_id=manifest_id, revision=1, root_ref=root_ref,
            )
            self._store_run_workspace(record)
            return self.public_run_workspace(record)
        except Exception:
            if created_path and os.path.exists(created_path):
                if strategy_value is WorkspaceStrategy.GIT_WORKTREE:
                    subprocess.run(
                        ["git", "-C", source_path, "worktree", "remove", "--force",
                         created_path], capture_output=True, text=True, timeout=30,
                        check=False,
                    )
                shutil.rmtree(created_path)
            if original_lock:
                lock_key = f"workspaces/projects/{project_workspace_id}/original-write-lock"
                holder, _ = self.store.kv_get(lock_key)
                if holder == run_id:
                    self.store.kv_delete(lock_key)
            raise

    def _provision_demo(self, req_id: str, run_id: str) -> dict[str, Any]:
        if not self.demo_mode:
            raise ValidationError("Demo 临时工作区未启用", code="DEMO_MODE_REQUIRED")
        root = self.security.allowed_roots.get(self.demo_root_alias)
        if not root:
            raise ValidationError("Demo 根目录未配置", code="DEMO_ROOT_NOT_CONFIGURED")
        os.makedirs(root, exist_ok=True)
        created = tempfile.mkdtemp(prefix=f"harness-{run_id}-", dir=root)
        relative = os.path.relpath(created, root).replace(os.sep, "/")
        retention = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=self.retention_days)
        ).isoformat().replace("+00:00", "Z")
        record = RunWorkspace(
            run_workspace_id=f"rws_{uuid.uuid4().hex}", req_id=req_id,
            run_id=run_id, project_workspace_id=None,
            strategy=WorkspaceStrategy.DEMO_TEMP, resolved_commit_sha=None,
            status=WorkspaceStatus.READY, read_write=True, temporary_demo=True,
            retention_until=retention, snapshot_manifest_id=None, revision=1,
            root_ref=self.security.make_root_ref(self.demo_root_alias, relative),
        )
        try:
            self._store_run_workspace(record)
        except Exception:
            shutil.rmtree(created)
            raise
        return self.public_run_workspace(record)

    def _provision_target(self, run_id: str) -> tuple[str, str]:
        root = self.security.allowed_roots.get(self.provision_root_alias)
        if not root:
            raise ValidationError(
                "Run Workspace 根目录未配置", code="PROVISION_ROOT_NOT_CONFIGURED"
            )
        os.makedirs(root, exist_ok=True)
        relative = f"runs/{run_id}"
        target = os.path.join(root, "runs", run_id)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.lexists(target):
            raise ConflictError("Run Workspace 目录已存在", code="RUN_WORKSPACE_EXISTS")
        return self.security.make_root_ref(self.provision_root_alias, relative), target

    def _capture_dirty_snapshot(
        self, req_id: str, run_id: str, path: str, head_sha: Optional[str]
    ) -> str:
        snapshot = {
            "head_sha": head_sha,
            "files": self._manifest_entries(path),
            "staged_patch": self._git(path, "diff", "--cached", required=True),
            "unstaged_patch": self._git(path, "diff", required=True),
            "untracked": self._git(
                path, "ls-files", "--others", "--exclude-standard", required=True
            ).splitlines(),
        }
        encoded = json.dumps(snapshot, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 5 * 1024 * 1024:
            raise ValidationError("脏目录快照超过限制", code="DIRTY_SNAPSHOT_TOO_LARGE")
        manifest_id = f"wsm_{uuid.uuid4().hex}"
        self.store.kv_put(
            f"workflows/{req_id}/runs/{run_id}/workspace/manifests/{manifest_id}",
            encoded,
        )
        return manifest_id

    def _capture_workspace_manifest(
        self, req_id: str, run_id: str, path: str,
        head_sha: Optional[str],
    ) -> str:
        manifest = {
            "head_sha": head_sha,
            "files": self._manifest_entries(path),
            "staged_patch": "", "unstaged_patch": "", "untracked": [],
        }
        manifest_id = f"wsm_{uuid.uuid4().hex}"
        self.store.kv_put(
            f"workflows/{req_id}/runs/{run_id}/workspace/manifests/{manifest_id}",
            json.dumps(manifest, ensure_ascii=False),
        )
        return manifest_id

    @staticmethod
    def _manifest_entries(path: str) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for current, directories, files in os.walk(path, followlinks=False):
            directories[:] = sorted(
                name for name in directories
                if name != ".git" and not os.path.islink(os.path.join(current, name))
            )
            for name in sorted(files):
                target = os.path.join(current, name)
                relative = os.path.relpath(target, path).replace(os.sep, "/")
                leaf = relative.rsplit("/", 1)[-1]
                if any(fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(leaf, pattern)
                       for pattern in (".env", ".env*", "*.pem", "*.key", "id_rsa*",
                                       "*credentials*", "*token*")):
                    continue
                try:
                    stat = os.lstat(target)
                    if os.path.islink(target):
                        entries.append({"path": relative, "type": "symlink",
                                        "size": stat.st_size, "sha256": None})
                        continue
                    digest = None
                    if stat.st_size <= 10 * 1024 * 1024:
                        hasher = hashlib.sha256()
                        with open(target, "rb") as handle:
                            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                                hasher.update(chunk)
                        digest = hasher.hexdigest()
                    entries.append({"path": relative, "type": "file",
                                    "size": stat.st_size, "sha256": digest})
                except OSError:
                    continue
                if len(entries) >= 50000:
                    raise ValidationError(
                        "Workspace 文件数量超过 Manifest 限制",
                        code="WORKSPACE_MANIFEST_TOO_LARGE",
                    )
        return entries

    def _store_run_workspace(self, record: RunWorkspace) -> None:
        key = f"workflows/{record.req_id}/runs/{record.run_id}/workspace/record"
        if not self.store.kv_put(key, json.dumps(record.to_dict()), cas=0):
            raise ConflictError("Run 已存在 Workspace", code="RUN_WORKSPACE_EXISTS")

    def _transition_run_workspace(
        self, req_id: str, run_id: str, status: WorkspaceStatus
    ) -> dict[str, Any]:
        key = f"workflows/{req_id}/runs/{run_id}/workspace/record"
        raw, index = self.store.kv_get(key)
        if not raw:
            raise NotFoundError("Run Workspace 不存在", code="RUN_WORKSPACE_NOT_FOUND")
        current = _decode_run_workspace(raw)
        updated = current.transition(status)
        if updated is current:
            return self.public_run_workspace(updated)
        if not self.store.kv_put(key, json.dumps(updated.to_dict()), cas=index):
            raise ConflictError("Run Workspace 状态已变化", code="WORKSPACE_STATE_CONFLICT")
        return self.public_run_workspace(updated)

    def _replace_run_workspace(self, current: RunWorkspace, **changes) -> dict[str, Any]:
        key = f"workflows/{current.req_id}/runs/{current.run_id}/workspace/record"
        raw, index = self.store.kv_get(key)
        if not raw:
            raise NotFoundError("Run Workspace 不存在", code="RUN_WORKSPACE_NOT_FOUND")
        latest = _decode_run_workspace(raw)
        if latest.revision != current.revision:
            raise ConflictError("Run Workspace 状态已变化", code="WORKSPACE_STATE_CONFLICT")
        updated = replace(latest, revision=latest.revision + 1, **changes)
        if not self.store.kv_put(key, json.dumps(updated.to_dict()), cas=index):
            raise ConflictError("Run Workspace 状态已变化", code="WORKSPACE_STATE_CONFLICT")
        return self.public_run_workspace(updated)

    def finish_run_workspace(self, req_id: str, run_id: str) -> dict[str, Any] | None:
        """Release execution locks and classify a completed Run Workspace."""
        try:
            workspace = _decode_run_workspace(json.dumps(
                self.get_run_workspace(req_id, run_id, include_root=True)
            ))
        except NotFoundError:
            return None
        root = self.security.resolve_root_ref(workspace.root_ref) if workspace.root_ref else ""
        preserve = workspace.strategy is WorkspaceStrategy.CONTROLLED_COPY
        if workspace.strategy is WorkspaceStrategy.DEMO_TEMP:
            preserve = bool(root and os.path.isdir(root) and any(os.scandir(root)))
        elif root and os.path.isdir(root) and self._git_ok(root, "rev-parse", "--git-dir"):
            dirty = bool(self._git(root, "status", "--porcelain", required=False))
            head = self._git(root, "rev-parse", "HEAD", required=False)
            preserve = dirty or bool(
                workspace.resolved_commit_sha and head
                and head != workspace.resolved_commit_sha
            )
        if workspace.strategy is WorkspaceStrategy.ORIGINAL and workspace.project_workspace_id:
            lock_key = (f"workspaces/projects/{workspace.project_workspace_id}/"
                        "original-write-lock")
            holder, _ = self.store.kv_get(lock_key)
            if holder == run_id:
                self.store.kv_delete(lock_key)
        target = WorkspaceStatus.RETAINED if preserve else WorkspaceStatus.CLEANUP_PENDING
        if workspace.status in {WorkspaceStatus.READY, WorkspaceStatus.ACTIVE,
                                WorkspaceStatus.FAILED}:
            return self._transition_run_workspace(req_id, run_id, target)
        return self.public_run_workspace(workspace)

    def request_cleanup(self, req_id: str, run_id: str) -> dict[str, Any]:
        workspace = _decode_run_workspace(json.dumps(
            self.get_run_workspace(req_id, run_id, include_root=True)
        ))
        if workspace.status is WorkspaceStatus.CLEANUP_PENDING:
            return self.public_run_workspace(workspace)
        if workspace.status is not WorkspaceStatus.RETAINED:
            raise ValidationError(
                "只有 RETAINED Workspace 可以申请清理",
                code="WORKSPACE_NOT_RETAINED",
            )
        return self._transition_run_workspace(
            req_id, run_id, WorkspaceStatus.CLEANUP_PENDING
        )

    def trash_run_workspace(self, req_id: str, run_id: str) -> dict[str, Any]:
        workspace = _decode_run_workspace(json.dumps(
            self.get_run_workspace(req_id, run_id, include_root=True)
        ))
        if workspace.status is not WorkspaceStatus.CLEANUP_PENDING:
            raise ValidationError(
                "Workspace 尚未进入 CLEANUP_PENDING", code="WORKSPACE_NOT_CLEANUP_PENDING"
            )
        original_root_ref = workspace.root_ref
        trashed_root_ref = original_root_ref
        if workspace.strategy is not WorkspaceStrategy.ORIGINAL:
            trash_relative = f".trash/{workspace.run_workspace_id}"
            trashed_root_ref = self.security.make_root_ref(
                self.provision_root_alias, trash_relative
            )
            source = self.security.resolve_root_ref(original_root_ref, must_exist=False)
            target = self.security.resolve_root_ref(trashed_root_ref, must_exist=False)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.lexists(target):
                raise ConflictError("Trash 目标已存在", code="WORKSPACE_TRASH_EXISTS")
            if os.path.exists(source):
                shutil.move(source, target)
        metadata_base = f"workflows/{req_id}/runs/{run_id}/workspace/retention"
        self.store.kv_put(f"{metadata_base}/origin_root_ref", original_root_ref or "")
        self.store.kv_put(f"{metadata_base}/trashed_at", _now_iso())
        return self._replace_run_workspace(
            workspace, status=WorkspaceStatus.TRASHED, root_ref=trashed_root_ref
        )

    def restore_run_workspace(self, req_id: str, run_id: str) -> dict[str, Any]:
        workspace = _decode_run_workspace(json.dumps(
            self.get_run_workspace(req_id, run_id, include_root=True)
        ))
        if workspace.status is not WorkspaceStatus.TRASHED:
            raise ValidationError("Workspace 不在 Trash 中", code="WORKSPACE_NOT_TRASHED")
        metadata_base = f"workflows/{req_id}/runs/{run_id}/workspace/retention"
        origin_root_ref, _ = self.store.kv_get(f"{metadata_base}/origin_root_ref")
        if not origin_root_ref:
            raise ValidationError("Trash 缺少恢复位置", code="WORKSPACE_TRASH_METADATA_MISSING")
        if workspace.strategy is not WorkspaceStrategy.ORIGINAL:
            source = self.security.resolve_root_ref(workspace.root_ref)
            target = self.security.resolve_root_ref(origin_root_ref, must_exist=False)
            if os.path.lexists(target):
                raise ConflictError("原恢复位置已被占用", code="WORKSPACE_RESTORE_CONFLICT")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(source, target)
        restored = self._replace_run_workspace(
            workspace, status=WorkspaceStatus.READY, root_ref=origin_root_ref
        )
        self.store.kv_delete(metadata_base, recurse=True)
        return restored

    def purge_run_workspace(
        self, req_id: str, run_id: str, *, force: bool = False,
        now: Optional[datetime.datetime] = None,
    ) -> dict[str, Any]:
        workspace = _decode_run_workspace(json.dumps(
            self.get_run_workspace(req_id, run_id, include_root=True)
        ))
        if workspace.status is not WorkspaceStatus.TRASHED:
            raise ValidationError("Workspace 不在 Trash 中", code="WORKSPACE_NOT_TRASHED")
        metadata_base = f"workflows/{req_id}/runs/{run_id}/workspace/retention"
        trashed_at, _ = self.store.kv_get(f"{metadata_base}/trashed_at")
        current_time = now or datetime.datetime.now(datetime.timezone.utc)
        if not force:
            if not trashed_at:
                raise ValidationError("Trash 缺少时间信息", code="WORKSPACE_TRASH_METADATA_MISSING")
            eligible_at = datetime.datetime.fromisoformat(
                trashed_at.replace("Z", "+00:00")
            ) + datetime.timedelta(hours=24)
            if current_time < eligible_at:
                raise ConflictError(
                    "Workspace 仍在 24 小时可恢复窗口内",
                    code="WORKSPACE_TRASH_GRACE_PERIOD",
                    details={"eligible_at": eligible_at.isoformat().replace("+00:00", "Z")},
                )
        if workspace.strategy is not WorkspaceStrategy.ORIGINAL and workspace.root_ref:
            target = self.security.resolve_root_ref(workspace.root_ref, must_exist=False)
            if os.path.exists(target):
                shutil.rmtree(target)
            if workspace.strategy is WorkspaceStrategy.GIT_WORKTREE and workspace.project_workspace_id:
                project = _decode(json.dumps(self.get(workspace.project_workspace_id)))
                source = self.security.resolve_root_ref(project.root_ref)
                subprocess.run(
                    ["git", "-C", source, "worktree", "prune"],
                    capture_output=True, text=True, timeout=30, check=False,
                )
        deleted = self._replace_run_workspace(
            workspace, status=WorkspaceStatus.DELETED
        )
        self.store.kv_delete(metadata_base, recurse=True)
        return deleted

    def get_run_workspace(
        self, req_id: str, run_id: str, *, include_root: bool = False
    ) -> dict[str, Any]:
        raw, _ = self.store.kv_get(
            f"workflows/{req_id}/runs/{run_id}/workspace/record"
        )
        if not raw:
            raise NotFoundError("Run Workspace 不存在", code="RUN_WORKSPACE_NOT_FOUND")
        record = _decode_run_workspace(raw)
        return record.to_dict() if include_root else self.public_run_workspace(record)

    def get_run_manifest(self, req_id: str, run_id: str) -> dict[str, Any]:
        workspace = self.get_run_workspace(req_id, run_id, include_root=True)
        manifest_id = workspace.get("snapshot_manifest_id")
        if not manifest_id:
            return {"manifest_id": None, "files": [], "req_id": req_id, "run_id": run_id}
        raw, _ = self.store.kv_get(
            f"workflows/{req_id}/runs/{run_id}/workspace/manifests/{manifest_id}"
        )
        if not raw:
            return {"manifest_id": manifest_id, "files": [], "req_id": req_id, "run_id": run_id}
        try:
            manifest = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            manifest = {}
        return {
            "manifest_id": manifest_id,
            "files": list(manifest.get("files", [])),
            "req_id": req_id, "run_id": run_id,
            "strategy": workspace.get("strategy"),
            "resolved_commit_sha": workspace.get("resolved_commit_sha"),
        }

    @staticmethod
    def public_run_workspace(record: RunWorkspace) -> dict[str, Any]:
        data = record.to_dict()
        data.pop("root_ref", None)
        return data

    def bind_attempt(
        self, *, req_id: str, run_id: str, task_id: str, attempt_id: str,
        write_scope: tuple[str, ...] = (), isolated_workspace_id: Optional[str] = None,
    ) -> dict[str, Any]:
        workspace = _decode_run_workspace(json.dumps(
            self.get_run_workspace(req_id, run_id, include_root=True)
        ))
        if workspace.status not in {WorkspaceStatus.READY, WorkspaceStatus.ACTIVE}:
            raise ValidationError("Run Workspace 尚未就绪", code="RUN_WORKSPACE_NOT_READY")
        if isolated_workspace_id:
            isolated = self._get_isolated_workspace(isolated_workspace_id)
            if isolated.get("req_id") != req_id or isolated.get("run_id") != run_id:
                raise ValidationError(
                    "隔离 Workspace 不属于当前 Run", code="ISOLATED_WORKSPACE_MISMATCH"
                )
            writable = bool(isolated.get("read_write"))
        else:
            writable = workspace.read_write
        lock_key = f"workspaces/runs/{run_id}/binding-lock"
        if not self.store.kv_put(lock_key, attempt_id, cas=0):
            raise ConflictError("Run Workspace Binding 正在并发更新", code="BINDING_LOCKED")
        try:
            if not isolated_workspace_id and writable and self.has_write_scope_conflict(
                req_id, run_id, task_id, write_scope
            ):
                raise ConflictError(
                    "可写 Task 的 write_scope 与运行中 Task 相交",
                    code="WRITE_SCOPE_CONFLICT",
                )
            binding = AttemptWorkspaceBinding(
                binding_id=f"awb_{uuid.uuid4().hex}", req_id=req_id, run_id=run_id,
                task_id=task_id, attempt_id=attempt_id,
                workspace_id=isolated_workspace_id or workspace.run_workspace_id,
                binding_type=(WorkspaceBindingType.ISOLATED if isolated_workspace_id
                              else WorkspaceBindingType.RUN_SHARED),
                write_scope=write_scope, base_commit_sha=workspace.resolved_commit_sha,
                writable=writable, bound_at=datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat().replace("+00:00", "Z"),
            )
            key = (f"workflows/{req_id}/runs/{run_id}/tasks/{task_id}/attempts/"
                   f"{attempt_id}/workspace-binding")
            if not self.store.kv_put(key, json.dumps(binding.to_dict()), cas=0):
                raise ConflictError("Attempt Workspace Binding 已存在", code="BINDING_EXISTS")
            if workspace.status is WorkspaceStatus.READY:
                self._transition_run_workspace(req_id, run_id, WorkspaceStatus.ACTIVE)
            return binding.to_dict()
        finally:
            holder, _ = self.store.kv_get(lock_key)
            if holder == attempt_id:
                self.store.kv_delete(lock_key)

    @staticmethod
    def _scope_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
        if not left or not right:
            return True
        for first in left:
            for second in right:
                if (fnmatch.fnmatch(first, second) or fnmatch.fnmatch(second, first)
                        or first.rstrip("/*") == second.rstrip("/*")):
                    return True
        return False

    def has_write_scope_conflict(
        self, req_id: str, run_id: str, task_id: str,
        write_scope: tuple[str, ...],
    ) -> bool:
        prefix = f"workflows/{req_id}/runs/{run_id}/tasks/"
        items, _ = self.store.kv_list(prefix, limit=1000)
        run_workspace = self.get_run_workspace(req_id, run_id, include_root=True)
        for item in items:
            if not item["key"].endswith("/workspace-binding"):
                continue
            try:
                binding = json.loads(item["value"])
            except (TypeError, json.JSONDecodeError):
                continue
            if binding.get("task_id") == task_id or binding.get("binding_type") == "ISOLATED":
                continue
            if binding.get("workspace_id") != run_workspace["run_workspace_id"]:
                continue
            status, _ = self.store.kv_get(
                f"workflows/{req_id}/tasks/{binding.get('task_id', '')}/status"
            )
            if status in {"DONE", "FAILED", "ABORTED", "SKIPPED_UPSTREAM_FAILED",
                          "SUPERSEDED"}:
                continue
            if self._scope_overlap(
                tuple(write_scope), tuple(binding.get("write_scope") or ())
            ):
                return True
        return False

    def provision_isolated_workspace(
        self, *, req_id: str, run_id: str, task_id: str, attempt_id: str,
    ) -> dict[str, Any]:
        """Create an isolated controlled copy for an explicitly scheduled Attempt."""
        workspace = self.get_run_workspace(req_id, run_id, include_root=True)
        source = self.security.resolve_root_ref(workspace["root_ref"])
        isolated_id = f"rwi_{uuid.uuid4().hex}"
        relative = f"isolated/{run_id}/{task_id}/{attempt_id}"
        root_ref = self.security.make_root_ref(self.provision_root_alias, relative)
        target = self.security.resolve_root_ref(root_ref, must_exist=False)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.lexists(target):
            raise ConflictError("隔离 Workspace 目录已存在", code="ISOLATED_WORKSPACE_EXISTS")
        shutil.copytree(source, target, symlinks=False, ignore=shutil.ignore_patterns(".git"))
        # Controlled copies are made independently mergeable. The baseline
        # commit is internal metadata and never escapes through the API.
        if self._git_ok(source, "rev-parse", "--git-dir"):
            subprocess.run(["git", "-C", target, "init", "-q"], check=False,
                           capture_output=True, text=True)
            subprocess.run(["git", "-C", target, "add", "-A"], check=False,
                           capture_output=True, text=True)
            subprocess.run([
                "git", "-C", target, "-c", "user.name=harness", "-c",
                "user.email=harness@localhost", "commit", "-qm", "baseline",
            ], check=False, capture_output=True, text=True)
        record = {
            "workspace_id": isolated_id, "req_id": req_id, "run_id": run_id,
            "task_id": task_id, "attempt_id": attempt_id, "root_ref": root_ref,
            "read_write": bool(workspace["read_write"]), "status": "READY",
            "created_at": _now_iso(),
        }
        if not self.store.kv_put(
            f"workspaces/isolated/{isolated_id}/record", json.dumps(record), cas=0
        ):
            shutil.rmtree(target)
            raise ConflictError("隔离 Workspace ID 冲突", code="ISOLATED_WORKSPACE_EXISTS")
        return {key: value for key, value in record.items() if key != "root_ref"}

    def _get_isolated_workspace(self, workspace_id: str) -> dict[str, Any]:
        raw, _ = self.store.kv_get(f"workspaces/isolated/{workspace_id}/record")
        if not raw:
            raise NotFoundError(
                "隔离 Workspace 不存在", code="ISOLATED_WORKSPACE_NOT_FOUND"
            )
        return json.loads(raw)

    def discard_isolated_workspace(self, workspace_id: str) -> None:
        record = self._get_isolated_workspace(workspace_id)
        root = self.security.resolve_root_ref(record["root_ref"], must_exist=False)
        if os.path.isdir(root):
            shutil.rmtree(root)
        self.store.kv_delete(f"workspaces/isolated/{workspace_id}/record")

    def get_attempt_binding(
        self, req_id: str, run_id: str, task_id: str, attempt_id: str
    ) -> dict[str, Any]:
        key = (f"workflows/{req_id}/runs/{run_id}/tasks/{task_id}/attempts/"
               f"{attempt_id}/workspace-binding")
        raw, _ = self.store.kv_get(key)
        if not raw:
            raise NotFoundError("Attempt Workspace Binding 不存在", code="BINDING_NOT_FOUND")
        return json.loads(raw)

    def list_attempts(self, req_id: str, run_id: str, task_id: str) -> list[dict[str, Any]]:
        prefix = f"workflows/{req_id}/runs/{run_id}/tasks/{task_id}/attempts/"
        items, cursor = self.store.kv_list(prefix, limit=1000)
        if cursor is not None:
            raise RuntimeError("attempt history limit exceeded")
        attempts = []
        for item in items:
            if not item["key"].endswith("/workspace-binding"):
                continue
            try:
                attempts.append(json.loads(item["value"]))
            except (TypeError, json.JSONDecodeError):
                continue
        return sorted(attempts, key=lambda item: item.get("bound_at", ""))

    def resolve_attempt_path(
        self, req_id: str, run_id: str, task_id: str, attempt_id: str
    ) -> str:
        binding = self.get_attempt_binding(req_id, run_id, task_id, attempt_id)
        workspace = self.get_run_workspace(req_id, run_id, include_root=True)
        if binding["workspace_id"] != workspace["run_workspace_id"]:
            isolated = self._get_isolated_workspace(binding["workspace_id"])
            if isolated.get("req_id") != req_id or isolated.get("run_id") != run_id:
                raise ValidationError(
                    "隔离 Workspace 与 Attempt 不匹配", code="ISOLATED_WORKSPACE_MISMATCH"
                )
            return self.security.resolve_root_ref(isolated["root_ref"])
        return self.security.resolve_root_ref(workspace["root_ref"])

    @staticmethod
    def _git_ok(path: str, *args: str) -> bool:
        return bool(WorkspaceManager._git(path, *args, required=False))

    @staticmethod
    def _git(path: str, *args: str, required: bool) -> str:
        process = subprocess.run(
            ["git", "-C", path, *args], capture_output=True, text=True,
            timeout=15, check=False,
        )
        if process.returncode != 0:
            if required:
                raise ValidationError(
                    "Git Workspace 检查失败", code="GIT_PREFLIGHT_FAILED",
                    details={"stderr": process.stderr[-500:]},
                )
            return ""
        return process.stdout.strip()
