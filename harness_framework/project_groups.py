"""Project Group ownership, membership and Workflow relationship service."""
from __future__ import annotations

import datetime
import json
import uuid
from typing import Any, Optional

from .api_errors import ConflictError, NotFoundError, ValidationError
from .auth import Role, member_key_segment
from .kv_store_protocol import KVStore
from .workspace_models import ProjectGroup, ProjectGroupStatus
from .kv_pagination import paginate_items


UNASSIGNED_GROUP_ID = "unassigned"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _decode_group(raw: str) -> ProjectGroup:
    data = json.loads(raw)
    return ProjectGroup(
        group_id=data["group_id"],
        name=data["name"],
        description=data.get("description", ""),
        status=ProjectGroupStatus(data["status"]),
        default_workspace_id=data.get("default_workspace_id"),
        policy=dict(data.get("policy", {})),
        created_by=data["created_by"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        revision=int(data.get("revision", 1)),
    )


class ProjectGroupService:
    def __init__(self, store: KVStore):
        self.store = store

    @staticmethod
    def virtual_unassigned() -> dict[str, Any]:
        return {
            "group_id": UNASSIGNED_GROUP_ID,
            "name": "未分组",
            "description": "尚未迁移到项目组的旧工作流",
            "status": ProjectGroupStatus.ACTIVE.value,
            "default_workspace_id": None,
            "policy": {},
            "virtual": True,
            "revision": 1,
        }

    def create(
        self, *, name: str, description: str, actor: str,
        policy: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("项目组名称不能为空", code="GROUP_NAME_REQUIRED")
        if not isinstance(description, str):
            raise ValidationError("项目组说明必须是字符串")
        group_id = f"grp_{uuid.uuid4().hex}"
        now = _now_iso()
        record = ProjectGroup(
            group_id=group_id,
            name=name.strip(),
            description=description.strip(),
            status=ProjectGroupStatus.ACTIVE,
            default_workspace_id=None,
            policy=dict(policy or {}),
            created_by=actor,
            created_at=now,
            updated_at=now,
        )
        key = f"project-groups/{group_id}/record"
        if not self.store.kv_put(key, json.dumps(record.to_dict()), cas=0):
            raise ConflictError("项目组 ID 冲突", code="GROUP_ID_CONFLICT")
        member = {
            "subject_id": actor, "role": Role.OWNER.value,
            "created_at": now, "updated_at": now, "revision": 1,
        }
        if not self.store.kv_put(
            f"project-groups/{group_id}/members/{member_key_segment(actor)}",
            json.dumps(member), cas=0,
        ):
            self.store.kv_delete(key)
            raise ConflictError("无法建立项目组 Owner", code="GROUP_OWNER_CONFLICT")
        return record.to_dict()

    def get(self, group_id: str) -> dict[str, Any]:
        if group_id == UNASSIGNED_GROUP_ID:
            return self.virtual_unassigned()
        raw, _ = self.store.kv_get(f"project-groups/{group_id}/record")
        if not raw:
            raise NotFoundError(f"项目组 {group_id} 不存在", code="GROUP_NOT_FOUND")
        return _decode_group(raw).to_dict()

    def list(
        self, *, status: Optional[str] = None, cursor: Optional[str] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        if status and status not in {item.value for item in ProjectGroupStatus}:
            raise ValidationError("无效的项目组状态", code="INVALID_GROUP_STATUS")
        raw_cursor = None
        records: list[dict[str, Any]] = []
        while True:
            items, raw_next = self.store.kv_list(
                "project-groups/", cursor=raw_cursor, limit=1000
            )
            for item in items:
                if not item["key"].endswith("/record"):
                    continue
                try:
                    group = _decode_group(item["value"]).to_dict()
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if status is None or group["status"] == status:
                    records.append({
                        "key": f"project-group-records/{group['group_id']}",
                        "value": group,
                        "modify_index": item["modify_index"],
                    })
            if raw_next is None:
                break
            raw_cursor = raw_next
        page, next_cursor = paginate_items(
            records, prefix="project-group-records/", cursor=cursor, limit=limit
        )
        groups = [item["value"] for item in page]
        if cursor is None and (status in (None, ProjectGroupStatus.ACTIVE.value)):
            groups.insert(0, self.virtual_unassigned())
        return {"project_groups": groups, "next_cursor": next_cursor}

    def update(
        self, group_id: str, *, expected_revision: int, changes: dict[str, Any]
    ) -> dict[str, Any]:
        if group_id == UNASSIGNED_GROUP_ID:
            raise ValidationError("虚拟未分组项目组不能修改", code="VIRTUAL_GROUP_READ_ONLY")
        key = f"project-groups/{group_id}/record"
        raw, modify_index = self.store.kv_get(key)
        if not raw:
            raise NotFoundError(f"项目组 {group_id} 不存在", code="GROUP_NOT_FOUND")
        record = _decode_group(raw)
        if record.revision != expected_revision:
            raise ConflictError(
                "项目组已被其他请求修改", details={"current_revision": record.revision}
            )
        allowed = {"name", "description", "default_workspace_id", "policy", "status"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValidationError(
                "包含不支持的项目组字段", details={"fields": sorted(unknown)}
            )
        data = record.to_dict()
        if "status" in changes:
            if changes["status"] != ProjectGroupStatus.ARCHIVED.value:
                raise ValidationError("项目组只允许归档", code="INVALID_GROUP_TRANSITION")
            data["status"] = ProjectGroupStatus.ARCHIVED.value
        if "name" in changes:
            if not isinstance(changes["name"], str) or not changes["name"].strip():
                raise ValidationError("项目组名称不能为空", code="GROUP_NAME_REQUIRED")
            data["name"] = changes["name"].strip()
        for field in ("description", "default_workspace_id", "policy"):
            if field in changes:
                data[field] = changes[field]
        data["updated_at"] = _now_iso()
        data["revision"] = record.revision + 1
        updated = _decode_group(json.dumps(data))
        if not self.store.kv_put(key, json.dumps(updated.to_dict()), cas=modify_index):
            raise ConflictError("项目组已被其他请求修改")
        return updated.to_dict()

    def list_members(self, group_id: str) -> list[dict[str, Any]]:
        self._require_real_group(group_id)
        items, cursor = self.store.kv_list(
            f"project-groups/{group_id}/members/", limit=1000
        )
        if cursor is not None:
            raise RuntimeError("project group member limit exceeded")
        result = []
        for item in items:
            try:
                result.append(json.loads(item["value"]))
            except (TypeError, json.JSONDecodeError):
                continue
        return sorted(result, key=lambda member: member.get("subject_id", ""))

    def put_member(self, group_id: str, subject_id: str, role: str) -> dict[str, Any]:
        group = self._require_active(group_id)
        del group
        if not subject_id.strip():
            raise ValidationError("成员 subject 不能为空", code="MEMBER_SUBJECT_REQUIRED")
        try:
            normalized_role = Role(role.upper())
        except ValueError as exc:
            raise ValidationError("无效的项目组角色", code="INVALID_GROUP_ROLE") from exc
        key = f"project-groups/{group_id}/members/{member_key_segment(subject_id)}"
        existing, index = self.store.kv_get(key)
        now = _now_iso()
        revision = 1
        created_at = now
        if existing:
            old = json.loads(existing)
            revision = int(old.get("revision", 1)) + 1
            created_at = old.get("created_at", now)
        record = {
            "subject_id": subject_id, "role": normalized_role.value,
            "created_at": created_at, "updated_at": now, "revision": revision,
        }
        if not self.store.kv_put(key, json.dumps(record), cas=index if existing else 0):
            raise ConflictError("成员记录已被其他请求修改")
        return record

    def delete_member(self, group_id: str, subject_id: str) -> None:
        self._require_active(group_id)
        key = f"project-groups/{group_id}/members/{member_key_segment(subject_id)}"
        raw, _ = self.store.kv_get(key)
        if not raw:
            raise NotFoundError("项目组成员不存在", code="MEMBER_NOT_FOUND")
        member = json.loads(raw)
        if member.get("role") == Role.OWNER.value:
            owners = [
                item for item in self.list_members(group_id)
                if item.get("role") == Role.OWNER.value
            ]
            if len(owners) <= 1:
                raise ConflictError(
                    "不能移除项目组最后一个 Owner", code="LAST_OWNER_REQUIRED"
                )
        self.store.kv_delete(key)

    def assign_primary_workflow(self, group_id: str, req_id: str) -> None:
        self._require_active(group_id)
        workflow, _ = self.store.kv_get(f"workflows/{req_id}/dependencies")
        if workflow is None:
            raise NotFoundError(f"Workflow {req_id} 不存在", code="WORKFLOW_NOT_FOUND")
        pointer = f"workflows/{req_id}/project-group"
        current, _ = self.store.kv_get(pointer)
        if current and current != group_id:
            raise ConflictError(
                "Workflow 已有主项目组", code="WORKFLOW_PRIMARY_GROUP_EXISTS",
                details={"group_id": current},
            )
        if not current and not self.store.kv_put(pointer, group_id, cas=0):
            raise ConflictError("Workflow 主项目组绑定冲突")
        self.store.kv_put(f"project-groups/{group_id}/workflows/{req_id}", "primary")

    def add_reference(self, group_id: str, req_id: str, capabilities: list[str]) -> dict:
        self._require_active(group_id)
        primary, _ = self.store.kv_get(f"workflows/{req_id}/project-group")
        if not primary:
            raise ValidationError(
                "Workflow 必须先设置主项目组", code="WORKFLOW_PRIMARY_GROUP_REQUIRED"
            )
        if primary == group_id:
            raise ValidationError("主项目组不需要跨组引用", code="PRIMARY_GROUP_REFERENCE")
        allowed = {"workflow:read", "run:create", "task:message:queue", "task:message:interrupt"}
        if not set(capabilities).issubset(allowed):
            raise ValidationError("跨组引用包含无效 capability")
        record = {
            "req_id": req_id, "primary_group_id": primary,
            "capabilities": sorted(set(capabilities) | {"workflow:read"}),
            "created_at": _now_iso(),
        }
        self.store.kv_put(
            f"project-groups/{group_id}/references/{req_id}", json.dumps(record)
        )
        return record

    def list_workflows(self, group_id: str) -> list[dict[str, Any]]:
        if group_id == UNASSIGNED_GROUP_ID:
            workflows, _ = self.store.kv_get("workflows/", recurse=True)
            req_ids = {
                item["Key"].split("/")[1] for item in (workflows or [])
                if len(item["Key"].split("/")) > 2
            }
            result = []
            for req_id in sorted(req_ids):
                owner, _ = self.store.kv_get(f"workflows/{req_id}/project-group")
                if not owner:
                    result.append({"req_id": req_id, "relationship": "unassigned"})
            return result
        self._require_real_group(group_id)
        result: list[dict[str, Any]] = []
        for kind, relationship in (("workflows", "primary"), ("references", "reference")):
            items, _ = self.store.kv_list(
                f"project-groups/{group_id}/{kind}/", limit=1000
            )
            for item in items:
                req_id = item["key"].rsplit("/", 1)[-1]
                entry = {"req_id": req_id, "relationship": relationship}
                if relationship == "reference":
                    entry.update(json.loads(item["value"]))
                result.append(entry)
        return sorted(result, key=lambda item: item["req_id"])

    def _require_real_group(self, group_id: str) -> dict[str, Any]:
        if group_id == UNASSIGNED_GROUP_ID:
            raise ValidationError("虚拟未分组不支持该操作", code="VIRTUAL_GROUP_READ_ONLY")
        return self.get(group_id)

    def _require_active(self, group_id: str) -> dict[str, Any]:
        group = self._require_real_group(group_id)
        if group["status"] != ProjectGroupStatus.ACTIVE.value:
            raise ConflictError("项目组已归档", code="GROUP_ARCHIVED")
        return group
