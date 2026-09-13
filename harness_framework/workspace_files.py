"""Safe file browsing and optimistic writes through Attempt Workspace Binding."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import subprocess
import tempfile
import sys
from typing import Any, Optional

from .api_errors import ConflictError, ValidationError
from .kv_pagination import paginate_items
from .kv_store_protocol import KVStore
from .workspace_manager import WorkspaceManager
from .event_journal import EventJournal


DEFAULT_SENSITIVE_PATTERNS = (
    ".git", ".git/**", ".env", ".env*", "*.pem", "*.key", "id_rsa*",
    "*credentials*", "*token*",
)
DEFAULT_EDIT_LIMIT = 2 * 1024 * 1024
DEFAULT_PREVIEW_LIMIT = 10 * 1024 * 1024

REGISTERED_ACTIONS = {
    "git-diff-check": {
        "label": "检查 Git diff 格式",
        "description": "运行 git diff --check，不修改工作区",
        "argv": ("git", "diff", "--check"),
        "timeout_seconds": 30,
    },
    "python-syntax-check": {
        "label": "检查 Python 语法",
        "description": "解析工作区中的 Python 文件，不执行项目代码",
        "argv": (sys.executable, "-c", (
            "import ast,pathlib,sys; root=pathlib.Path('.'); bad=[]; "
            "files=[p for p in root.rglob('*.py') if '.git' not in p.parts]; "
            "[(ast.parse(p.read_text(encoding='utf-8')),None) for p in files]; "
            "print(f'checked {len(files)} Python files')"
        )),
        "timeout_seconds": 60,
    },
}


class WorkspaceFileService:
    def __init__(self, store: KVStore, manager: WorkspaceManager,
                 event_journal: EventJournal | None = None):
        self.store = store
        self.manager = manager
        self.event_journal = event_journal or EventJournal(store)

    def _context(
        self, *, workspace_id: str, req_id: str, run_id: str,
        task_id: str, attempt_id: str,
    ) -> tuple[dict[str, Any], str]:
        binding = self.manager.get_attempt_binding(req_id, run_id, task_id, attempt_id)
        if binding.get("workspace_id") != workspace_id:
            raise ValidationError(
                "Workspace 与 Attempt Binding 不匹配", code="WORKSPACE_BINDING_MISMATCH"
            )
        root = self.manager.resolve_attempt_path(req_id, run_id, task_id, attempt_id)
        return binding, root

    def _policy(self, req_id: str, run_id: str) -> dict[str, Any]:
        run_workspace = self.manager.get_run_workspace(req_id, run_id, include_root=True)
        project_id = run_workspace.get("project_workspace_id")
        project = self.manager.get(project_id) if project_id else {"policy": {}}
        return dict(project.get("policy", {}))

    def _group_id(self, req_id: str, run_id: str) -> str:
        workspace = self.manager.get_run_workspace(req_id, run_id, include_root=True)
        project_id = workspace.get("project_workspace_id")
        if not project_id:
            return "unassigned"
        return self.manager.get(project_id)["group_id"]

    @staticmethod
    def _sensitive(path: str, policy: dict[str, Any]) -> bool:
        patterns = [*DEFAULT_SENSITIVE_PATTERNS, *policy.get("sensitive_patterns", [])]
        name = path.rsplit("/", 1)[-1]
        return any(fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(name, pattern)
                   for pattern in patterns)

    @staticmethod
    def _in_write_scope(path: str, binding: dict[str, Any]) -> bool:
        scopes = binding.get("write_scope") or []
        return not scopes or any(
            fnmatch.fnmatch(path, scope) or (
                scope.endswith("/**") and path == scope[:-3]
            ) for scope in scopes
        )

    def tree(
        self, *, workspace_id: str, req_id: str, run_id: str, task_id: str,
        attempt_id: str, path: str = "", cursor: Optional[str] = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        _, root = self._context(
            workspace_id=workspace_id, req_id=req_id, run_id=run_id,
            task_id=task_id, attempt_id=attempt_id,
        )
        policy = self._policy(req_id, run_id)
        if path:
            normalized = self.manager.security.validate_relative_path(path)
            directory = self.manager.security.resolve_child_path(root, normalized)
        else:
            normalized, directory = "", root
        if not os.path.isdir(directory):
            raise ValidationError("路径不是目录", code="WORKSPACE_NOT_DIRECTORY")
        entries = []
        with os.scandir(directory) as iterator:
            for entry in iterator:
                relative = f"{normalized}/{entry.name}".strip("/")
                if self._sensitive(relative, policy):
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                entries.append({
                    "key": f"workspace-tree/{workspace_id}/{normalized}/{entry.name}",
                    "value": {
                        "name": entry.name, "path": relative,
                        "type": ("symlink" if entry.is_symlink()
                                 else "directory" if entry.is_dir(follow_symlinks=False)
                                 else "file"),
                        "size": info.st_size,
                    },
                    "modify_index": int(info.st_mtime_ns),
                })
        page, next_cursor = paginate_items(
            entries, prefix=f"workspace-tree/{workspace_id}/{normalized}/",
            cursor=cursor, limit=limit,
        )
        return {"path": normalized, "entries": [item["value"] for item in page],
                "next_cursor": next_cursor}

    def read_file(
        self, *, workspace_id: str, req_id: str, run_id: str, task_id: str,
        attempt_id: str, path: str,
    ) -> dict[str, Any]:
        binding, root = self._context(
            workspace_id=workspace_id, req_id=req_id, run_id=run_id,
            task_id=task_id, attempt_id=attempt_id,
        )
        policy = self._policy(req_id, run_id)
        normalized = self.manager.security.validate_relative_path(path)
        if self._sensitive(normalized, policy):
            raise ValidationError("文件被敏感路径策略禁止", code="SENSITIVE_FILE_FORBIDDEN")
        target = self.manager.security.resolve_child_path(root, normalized)
        if not os.path.isfile(target):
            raise ValidationError("路径不是文件", code="WORKSPACE_NOT_FILE")
        size = os.path.getsize(target)
        edit_limit = int(policy.get("max_edit_bytes", DEFAULT_EDIT_LIMIT))
        preview_limit = int(policy.get("max_preview_bytes", DEFAULT_PREVIEW_LIMIT))
        metadata = {
            "workspace_id": workspace_id, "binding_id": binding["binding_id"],
            "path": normalized, "size": size,
        }
        if size > preview_limit:
            return {**metadata, "content": None, "sha256": None, "binary": False,
                    "writable": False, "reason": "FILE_TOO_LARGE"}
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(target, flags)
        try:
            raw = b""
            while len(raw) <= preview_limit:
                chunk = os.read(fd, min(1024 * 1024, preview_limit + 1 - len(raw)))
                if not chunk:
                    break
                raw += chunk
        finally:
            os.close(fd)
        digest = hashlib.sha256(raw).hexdigest()
        try:
            content = raw.decode("utf-8")
            binary = "\x00" in content
        except UnicodeDecodeError:
            content, binary = None, True
        writable = (bool(binding.get("writable"))
                    and self._in_write_scope(normalized, binding)
                    and not binary and size <= edit_limit)
        return {**metadata, "content": None if binary else content,
                "sha256": digest, "encoding": "utf-8" if not binary else None,
                "binary": binary, "writable": writable,
                "reason": "BINARY_FILE" if binary else "FILE_TOO_LARGE_TO_EDIT" if size > edit_limit else None}

    def write_file(
        self, *, workspace_id: str, req_id: str, run_id: str, task_id: str,
        attempt_id: str, path: str, content: str, expected_sha256: Optional[str],
        idempotency_key: str, actor: str, reason: str,
    ) -> dict[str, Any]:
        binding, _ = self._context(
            workspace_id=workspace_id, req_id=req_id, run_id=run_id,
            task_id=task_id, attempt_id=attempt_id,
        )
        if not binding.get("writable"):
            raise ValidationError("当前 Attempt Workspace 只读", code="WORKSPACE_READ_ONLY")
        if not idempotency_key:
            raise ValidationError("Idempotency-Key 不能为空", code="IDEMPOTENCY_KEY_REQUIRED")
        if not isinstance(content, str):
            raise ValidationError("文件内容必须是 UTF-8 文本", code="INVALID_FILE_CONTENT")
        policy = self._policy(req_id, run_id)
        encoded = content.encode("utf-8")
        edit_limit = int(policy.get("max_edit_bytes", DEFAULT_EDIT_LIMIT))
        if len(encoded) > edit_limit:
            raise ValidationError("文件超过可编辑大小限制", code="FILE_TOO_LARGE_TO_EDIT")
        normalized = self.manager.security.validate_relative_path(path)
        if not self._in_write_scope(normalized, binding):
            raise ValidationError(
                "文件不在 Attempt 的 write_scope 内", code="WRITE_SCOPE_FORBIDDEN",
                details={"path": normalized},
            )
        if self._sensitive(normalized, policy):
            raise ValidationError("文件被敏感路径策略禁止", code="SENSITIVE_FILE_FORBIDDEN")
        request_hash = hashlib.sha256(json.dumps({
            "workspace_id": workspace_id, "attempt_id": attempt_id,
            "path": normalized, "content_sha256": hashlib.sha256(encoded).hexdigest(),
            "expected_sha256": expected_sha256,
        }, sort_keys=True).encode()).hexdigest()
        idem_key = f"http-idempotency/file-write/{hashlib.sha256(idempotency_key.encode()).hexdigest()}"
        existing, _ = self.store.kv_get(idem_key)
        if existing:
            record = json.loads(existing)
            if record.get("request_hash") != request_hash:
                raise ConflictError("幂等键已用于其他写入", code="IDEMPOTENCY_CONFLICT")
            return record["response"]
        root = self.manager.resolve_attempt_path(req_id, run_id, task_id, attempt_id)
        target = self.manager.security.resolve_child_path(
            root, normalized, must_exist=False
        )
        parent = os.path.dirname(target)
        if not os.path.isdir(parent):
            raise ValidationError("父目录不存在", code="WORKSPACE_PARENT_NOT_FOUND")
        current_hash = None
        if os.path.exists(target):
            current = self.read_file(
                workspace_id=workspace_id, req_id=req_id, run_id=run_id,
                task_id=task_id, attempt_id=attempt_id, path=normalized,
            )
            current_hash = current["sha256"]
        if current_hash != expected_sha256:
            raise ConflictError(
                "文件已被其他操作修改", code="FILE_VERSION_CONFLICT",
                details={"expected_sha256": expected_sha256, "current_sha256": current_hash},
            )
        fd, temporary = tempfile.mkstemp(prefix=".harness-write-", dir=parent)
        try:
            os.fchmod(fd, 0o644)
            with os.fdopen(fd, "wb", closefd=True) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            directory_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            if os.path.exists(temporary):
                os.unlink(temporary)
            raise
        event = self.event_journal.append(
            "WORKSPACE_FILE_CHANGED",
            subject={
                "group_id": self._group_id(req_id, run_id),
                "req_id": req_id, "run_id": run_id, "task_id": task_id,
                "attempt_id": attempt_id, "workspace_id": workspace_id,
            },
            actor={"type": "human", "id": actor},
            data={"path": normalized, "sha256": hashlib.sha256(encoded).hexdigest(),
                  "source": "human", "reason": reason},
        )
        response = {
            "workspace_id": workspace_id, "binding_id": binding["binding_id"],
            "path": normalized, "sha256": hashlib.sha256(encoded).hexdigest(),
            "event_id": event.event_id, "actor": actor, "reason": reason,
        }
        if not self.store.kv_put(idem_key, json.dumps({
            "request_hash": request_hash, "response": response,
        }), cas=0):
            raise ConflictError("幂等结果并发冲突", code="IDEMPOTENCY_CONFLICT")
        self.store.kv_put(
            f"audit/{response['event_id']}", json.dumps({
                "type": "WORKSPACE_FILE_CHANGED", "source": "human", **response,
            }, ensure_ascii=False),
        )
        return response

    def changes(self, *, workspace_id: str, req_id: str, run_id: str,
                task_id: str, attempt_id: str) -> list[dict[str, str]]:
        _, root = self._context(
            workspace_id=workspace_id, req_id=req_id, run_id=run_id,
            task_id=task_id, attempt_id=attempt_id,
        )
        process = subprocess.run(
            ["git", "-C", root, "status", "--porcelain=v1", "-z"],
            capture_output=True, timeout=15, check=False,
        )
        if process.returncode != 0:
            return []
        result = []
        for raw in process.stdout.split(b"\x00"):
            if not raw:
                continue
            text = raw.decode("utf-8", errors="replace")
            result.append({"status": text[:2], "path": text[3:]})
        return result

    def list_actions(
        self, *, workspace_id: str, req_id: str, run_id: str,
        task_id: str, attempt_id: str,
    ) -> list[dict[str, Any]]:
        self._context(
            workspace_id=workspace_id, req_id=req_id, run_id=run_id,
            task_id=task_id, attempt_id=attempt_id,
        )
        return [
            {"action_id": action_id, "label": spec["label"],
             "description": spec["description"],
             "timeout_seconds": spec["timeout_seconds"]}
            for action_id, spec in REGISTERED_ACTIONS.items()
        ]

    def run_action(
        self, *, workspace_id: str, req_id: str, run_id: str,
        task_id: str, attempt_id: str, action_id: str, actor: str,
    ) -> dict[str, Any]:
        _, root = self._context(
            workspace_id=workspace_id, req_id=req_id, run_id=run_id,
            task_id=task_id, attempt_id=attempt_id,
        )
        spec = REGISTERED_ACTIONS.get(action_id)
        if not spec:
            raise ValidationError("Action 未登记", code="ACTION_NOT_REGISTERED")
        process = subprocess.run(
            list(spec["argv"]), cwd=root, capture_output=True, text=True,
            timeout=int(spec["timeout_seconds"]), check=False,
            env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"},
        )
        output_limit = 64 * 1024
        stdout = process.stdout[-output_limit:]
        stderr = process.stderr[-output_limit:]
        event = self.event_journal.append(
            "WORKSPACE_ACTION_FINISHED",
            subject={"group_id": self._group_id(req_id, run_id), "req_id": req_id, "run_id": run_id, "task_id": task_id,
                     "attempt_id": attempt_id, "workspace_id": workspace_id},
            actor={"type": "human", "id": actor},
            data={"action_id": action_id, "exit_code": process.returncode},
        )
        return {"action_id": action_id, "exit_code": process.returncode,
                "stdout": stdout, "stderr": stderr, "event_id": event.event_id}

    def create_checkpoint(
        self, *, workspace_id: str, req_id: str, run_id: str,
        task_id: str, attempt_id: str, actor: str, message: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        binding, root = self._context(
            workspace_id=workspace_id, req_id=req_id, run_id=run_id,
            task_id=task_id, attempt_id=attempt_id,
        )
        if not binding.get("writable"):
            raise ValidationError("当前 Attempt Workspace 只读", code="WORKSPACE_READ_ONLY")
        if not idempotency_key:
            raise ValidationError("Idempotency-Key 不能为空", code="IDEMPOTENCY_KEY_REQUIRED")
        if not isinstance(message, str) or not message.strip():
            raise ValidationError("Checkpoint 说明不能为空", code="CHECKPOINT_MESSAGE_REQUIRED")
        digest = hashlib.sha256(idempotency_key.encode()).hexdigest()
        idem_key = f"http-idempotency/checkpoint/{digest}"
        existing, _ = self.store.kv_get(idem_key)
        if existing:
            return json.loads(existing)
        if not os.path.isdir(os.path.join(root, ".git")) and not self.manager._git_ok(root, "rev-parse", "--git-dir"):
            raise ValidationError("Checkpoint 需要 Git Workspace", code="GIT_WORKSPACE_REQUIRED")
        status = subprocess.run(
            ["git", "status", "--porcelain=v1"], cwd=root,
            capture_output=True, text=True, timeout=15, check=False,
        )
        if status.returncode != 0:
            raise ValidationError("无法读取 Git 状态", code="GIT_STATUS_FAILED")
        paths: list[str] = []
        policy = self._policy(req_id, run_id)
        for line in status.stdout.splitlines():
            relative = line[3:].strip()
            if " -> " in relative:
                relative = relative.split(" -> ", 1)[1]
            if self._sensitive(relative, policy):
                raise ValidationError(
                    "Checkpoint 包含敏感路径修改", code="SENSITIVE_FILE_FORBIDDEN",
                    details={"path": relative},
                )
            if not self._in_write_scope(relative, binding):
                raise ValidationError(
                    "Checkpoint 包含 write_scope 外修改",
                    code="WRITE_SCOPE_FORBIDDEN", details={"path": relative},
                )
            if relative:
                paths.append(relative)
        if not paths:
            raise ValidationError("没有可创建 Checkpoint 的修改", code="CHECKPOINT_EMPTY")
        add = subprocess.run(
            ["git", "add", "--", *paths], cwd=root, capture_output=True,
            text=True, timeout=30, check=False,
        )
        if add.returncode != 0:
            raise ValidationError("Checkpoint 暂存失败", code="CHECKPOINT_STAGE_FAILED")
        commit = subprocess.run(
            ["git", "-c", f"user.name={actor}", "-c",
             "user.email=harness@localhost", "-c", "commit.gpgsign=false",
             "-c", "core.hooksPath=/dev/null", "commit", "--no-verify",
             "-m", message.strip()],
            cwd=root, capture_output=True, text=True, timeout=60, check=False,
        )
        if commit.returncode != 0:
            raise ValidationError(
                "Checkpoint 提交失败", code="CHECKPOINT_COMMIT_FAILED",
                details={"stderr": commit.stderr[-1000:]},
            )
        commit_sha = self.manager._git(root, "rev-parse", "HEAD", required=True)
        event = self.event_journal.append(
            "WORKSPACE_CHECKPOINT_CREATED",
            subject={"group_id": self._group_id(req_id, run_id), "req_id": req_id, "run_id": run_id, "task_id": task_id,
                     "attempt_id": attempt_id, "workspace_id": workspace_id},
            actor={"type": "human", "id": actor},
            data={"commit_sha": commit_sha, "message": message.strip()},
        )
        response = {"checkpoint_id": f"checkpoint:{commit_sha}",
                    "commit_sha": commit_sha, "event_id": event.event_id}
        if not self.store.kv_put(idem_key, json.dumps(response), cas=0):
            winner, _ = self.store.kv_get(idem_key)
            if winner:
                return json.loads(winner)
            raise ConflictError("Checkpoint 幂等结果冲突", code="IDEMPOTENCY_CONFLICT")
        return response

    def search(
        self, *, workspace_id: str, req_id: str, run_id: str, task_id: str,
        attempt_id: str, query: str, limit: int = 100,
    ) -> list[dict[str, Any]]:
        _, root = self._context(
            workspace_id=workspace_id, req_id=req_id, run_id=run_id,
            task_id=task_id, attempt_id=attempt_id,
        )
        if not query.strip():
            raise ValidationError("搜索词不能为空", code="SEARCH_QUERY_REQUIRED")
        policy = self._policy(req_id, run_id)
        limit = max(1, min(int(limit), 500))
        needle = query.casefold()
        result = []
        visited = 0
        for current, directories, files in os.walk(root, followlinks=False):
            relative_dir = os.path.relpath(current, root).replace(os.sep, "/")
            if relative_dir == ".":
                relative_dir = ""
            directories[:] = [
                name for name in directories
                if not self._sensitive(f"{relative_dir}/{name}".strip("/"), policy)
                and not os.path.islink(os.path.join(current, name))
            ]
            for name in files:
                visited += 1
                if visited > 50000:
                    return result
                relative = f"{relative_dir}/{name}".strip("/")
                if self._sensitive(relative, policy) or needle not in relative.casefold():
                    continue
                result.append({"path": relative, "name": name})
                if len(result) >= limit:
                    return result
        return result
