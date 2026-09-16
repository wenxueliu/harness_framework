"""Filesystem trust boundary shared by workspace provisioning and file APIs."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Mapping

from .api_errors import ValidationError


class WorkspaceSecurity:
    def __init__(self, allowed_roots: Mapping[str, str]):
        self.allowed_roots = {
            alias: os.path.realpath(path) for alias, path in allowed_roots.items()
        }
        for alias, path in self.allowed_roots.items():
            if not alias or ":" in alias or "/" in alias:
                raise ValueError(f"invalid workspace root alias: {alias}")
            if not os.path.isabs(path):
                raise ValueError(f"workspace root must be absolute: {alias}")

    @staticmethod
    def make_root_ref(alias: str, relative_path: str) -> str:
        relative = WorkspaceSecurity.validate_relative_path(relative_path, allow_empty=True)
        return f"{alias}:{relative}"

    def make_root_ref_from_path(self, path: str) -> str:
        """Convert an allowed absolute directory into its canonical root_ref."""
        if not isinstance(path, str) or "\x00" in path or not os.path.isabs(path):
            raise ValidationError(
                "绝对路径必须是服务端文件系统中的绝对目录",
                code="INVALID_ABSOLUTE_PATH",
            )
        candidate = os.path.realpath(path)
        matches: list[tuple[str, str]] = []
        for alias, root in self.allowed_roots.items():
            try:
                if os.path.commonpath([root, candidate]) != root:
                    continue
            except ValueError:
                continue
            relative = os.path.relpath(candidate, root)
            matches.append((alias, "" if relative == "." else relative))
        if not matches:
            raise ValidationError(
                "Workspace 路径未落在允许的根目录内",
                code="WORKSPACE_ROOT_NOT_ALLOWED",
            )
        if not os.path.isdir(candidate):
            raise ValidationError("Workspace 目录不存在", code="WORKSPACE_NOT_FOUND")

        # Prefer the most specific configured root when roots overlap.
        alias, relative = max(
            matches, key=lambda item: len(self.allowed_roots[item[0]])
        )
        return self.make_root_ref(alias, relative)

    @staticmethod
    def validate_relative_path(path: str, *, allow_empty: bool = False) -> str:
        if not isinstance(path, str) or "\x00" in path or os.path.isabs(path):
            raise ValidationError("路径必须是安全的相对路径", code="INVALID_RELATIVE_PATH")
        normalized = path.replace("\\", "/").strip("/")
        parts = normalized.split("/") if normalized else []
        if any(part in {"", ".", ".."} for part in parts):
            raise ValidationError("路径包含不允许的段", code="INVALID_RELATIVE_PATH")
        if not normalized and not allow_empty:
            raise ValidationError("相对路径不能为空", code="INVALID_RELATIVE_PATH")
        return "/".join(parts)

    def resolve_root_ref(self, root_ref: str, *, must_exist: bool = True) -> str:
        if not isinstance(root_ref, str) or ":" not in root_ref:
            raise ValidationError("无效的 Workspace root_ref", code="INVALID_ROOT_REF")
        alias, relative = root_ref.split(":", 1)
        root = self.allowed_roots.get(alias)
        if root is None:
            raise ValidationError("Workspace 根目录未被允许", code="WORKSPACE_ROOT_NOT_ALLOWED")
        normalized = self.validate_relative_path(relative, allow_empty=True)
        candidate = os.path.join(root, *normalized.split("/")) if normalized else root
        resolved = os.path.realpath(candidate)
        try:
            contained = os.path.commonpath([root, resolved]) == root
        except ValueError:
            contained = False
        if not contained:
            raise ValidationError("Workspace 路径越过允许根目录", code="WORKSPACE_PATH_ESCAPE")
        if must_exist and not os.path.isdir(resolved):
            raise ValidationError("Workspace 目录不存在", code="WORKSPACE_NOT_FOUND")
        return resolved

    def resolve_child(self, root_ref: str, relative_path: str,
                      *, must_exist: bool = True, allow_symlinks: bool = False) -> str:
        root = self.resolve_root_ref(root_ref)
        return self.resolve_child_path(
            root, relative_path, must_exist=must_exist, allow_symlinks=allow_symlinks
        )

    def resolve_child_path(self, root: str, relative_path: str,
                           *, must_exist: bool = True,
                           allow_symlinks: bool = False) -> str:
        root = os.path.realpath(root)
        normalized = self.validate_relative_path(relative_path)
        target = os.path.realpath(os.path.join(root, *normalized.split("/")))
        if os.path.commonpath([root, target]) != root:
            raise ValidationError("文件路径越过 Workspace", code="WORKSPACE_PATH_ESCAPE")
        if must_exist and not Path(target).exists():
            raise ValidationError("Workspace 文件不存在", code="WORKSPACE_FILE_NOT_FOUND")
        if not allow_symlinks:
            current = root
            for part in normalized.split("/"):
                current = os.path.join(current, part)
                try:
                    mode = os.lstat(current).st_mode
                except FileNotFoundError:
                    if must_exist:
                        raise ValidationError(
                            "Workspace 文件不存在", code="WORKSPACE_FILE_NOT_FOUND"
                        )
                    break
                if stat.S_ISLNK(mode):
                    raise ValidationError(
                        "Workspace 文件路径包含符号链接", code="WORKSPACE_SYMLINK_FORBIDDEN"
                    )
        return target
