"""Immutable Dashboard workspace domain records and state transitions."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any, Optional


class ProjectGroupStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class WorkspaceSourceType(str, Enum):
    LOCAL_PATH = "LOCAL_PATH"
    GIT_CLONE = "GIT_CLONE"


class WorkspaceAccess(str, Enum):
    READ_ONLY = "READ_ONLY"
    READ_WRITE = "READ_WRITE"


class WorkspaceStrategy(str, Enum):
    ORIGINAL = "ORIGINAL"
    GIT_WORKTREE = "GIT_WORKTREE"
    CONTROLLED_COPY = "CONTROLLED_COPY"
    DEMO_TEMP = "DEMO_TEMP"


class WorkspaceStatus(str, Enum):
    PROVISIONING = "PROVISIONING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    RETAINED = "RETAINED"
    CLEANUP_PENDING = "CLEANUP_PENDING"
    TRASHED = "TRASHED"
    DELETED = "DELETED"
    FAILED = "FAILED"


class WorkspaceBindingType(str, Enum):
    RUN_SHARED = "RUN_SHARED"
    ISOLATED = "ISOLATED"


WORKSPACE_TRANSITIONS: dict[WorkspaceStatus, frozenset[WorkspaceStatus]] = {
    WorkspaceStatus.PROVISIONING: frozenset({
        WorkspaceStatus.READY, WorkspaceStatus.FAILED,
    }),
    WorkspaceStatus.READY: frozenset({
        WorkspaceStatus.ACTIVE, WorkspaceStatus.CLEANUP_PENDING,
        WorkspaceStatus.RETAINED, WorkspaceStatus.FAILED,
    }),
    WorkspaceStatus.ACTIVE: frozenset({
        WorkspaceStatus.RETAINED, WorkspaceStatus.CLEANUP_PENDING,
        WorkspaceStatus.FAILED,
    }),
    WorkspaceStatus.RETAINED: frozenset({WorkspaceStatus.CLEANUP_PENDING}),
    WorkspaceStatus.CLEANUP_PENDING: frozenset({
        WorkspaceStatus.RETAINED, WorkspaceStatus.TRASHED,
    }),
    WorkspaceStatus.TRASHED: frozenset({
        WorkspaceStatus.READY, WorkspaceStatus.DELETED,
    }),
    WorkspaceStatus.DELETED: frozenset(),
    WorkspaceStatus.FAILED: frozenset({WorkspaceStatus.CLEANUP_PENDING}),
}


def _require(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _enum_dict(record: Any) -> dict[str, Any]:
    result = asdict(record)
    for key, value in list(result.items()):
        if isinstance(value, Enum):
            result[key] = value.value
    return result


@dataclass(frozen=True)
class ProjectGroup:
    group_id: str
    name: str
    description: str
    status: ProjectGroupStatus
    default_workspace_id: Optional[str]
    policy: dict[str, Any]
    created_by: str
    created_at: str
    updated_at: str
    revision: int = 1

    def __post_init__(self) -> None:
        _require(self.group_id, "group_id")
        _require(self.name, "name")
        _require(self.created_by, "created_by")
        if self.revision < 1:
            raise ValueError("revision must be positive")

    def archive(self, *, updated_at: str) -> "ProjectGroup":
        if self.status is ProjectGroupStatus.ARCHIVED:
            return self
        return replace(
            self, status=ProjectGroupStatus.ARCHIVED,
            updated_at=updated_at, revision=self.revision + 1,
        )

    def to_dict(self) -> dict[str, Any]:
        return _enum_dict(self)


@dataclass(frozen=True)
class ProjectWorkspace:
    workspace_id: str
    group_id: str
    name: str
    source_type: WorkspaceSourceType
    root_ref: str
    git_url: Optional[str]
    default_ref: Optional[str]
    access: WorkspaceAccess
    status: WorkspaceStatus
    policy: dict[str, Any]
    revision: int = 1

    def __post_init__(self) -> None:
        for name in ("workspace_id", "group_id", "name", "root_ref"):
            _require(getattr(self, name), name)
        if self.source_type is WorkspaceSourceType.GIT_CLONE and not self.git_url:
            raise ValueError("git_url is required for GIT_CLONE")

    def to_dict(self) -> dict[str, Any]:
        return _enum_dict(self)


@dataclass(frozen=True)
class RunWorkspace:
    run_workspace_id: str
    req_id: str
    run_id: str
    project_workspace_id: Optional[str]
    strategy: WorkspaceStrategy
    resolved_commit_sha: Optional[str]
    status: WorkspaceStatus
    read_write: bool
    temporary_demo: bool
    retention_until: Optional[str]
    snapshot_manifest_id: Optional[str]
    revision: int = 1
    root_ref: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("run_workspace_id", "req_id", "run_id"):
            _require(getattr(self, name), name)
        if self.status not in {WorkspaceStatus.PROVISIONING, WorkspaceStatus.FAILED}:
            _require(self.root_ref or "", "root_ref")
        if self.strategy is WorkspaceStrategy.DEMO_TEMP and not self.temporary_demo:
            raise ValueError("DEMO_TEMP must be marked temporary_demo")
        if self.strategy is not WorkspaceStrategy.DEMO_TEMP and self.temporary_demo:
            raise ValueError("temporary_demo is only valid for DEMO_TEMP")

    def transition(self, status: WorkspaceStatus) -> "RunWorkspace":
        if status is self.status:
            return self
        if status not in WORKSPACE_TRANSITIONS[self.status]:
            raise ValueError(
                f"invalid workspace transition: {self.status.value} -> {status.value}"
            )
        return replace(self, status=status, revision=self.revision + 1)

    def to_dict(self) -> dict[str, Any]:
        return _enum_dict(self)


@dataclass(frozen=True)
class AttemptWorkspaceBinding:
    binding_id: str
    req_id: str
    run_id: str
    task_id: str
    attempt_id: str
    workspace_id: str
    binding_type: WorkspaceBindingType
    write_scope: tuple[str, ...]
    base_commit_sha: Optional[str]
    writable: bool
    bound_at: str

    def __post_init__(self) -> None:
        for name in (
            "binding_id", "req_id", "run_id", "task_id", "attempt_id",
            "workspace_id", "bound_at",
        ):
            _require(getattr(self, name), name)
        if any(not scope or scope.startswith("/") or ".." in scope.split("/")
               for scope in self.write_scope):
            raise ValueError("write_scope must contain safe relative patterns")

    def to_dict(self) -> dict[str, Any]:
        result = _enum_dict(self)
        result["write_scope"] = list(self.write_scope)
        return result
