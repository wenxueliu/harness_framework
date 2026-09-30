"""Template/version/instance domain service for the job-flow dashboard.

The legacy workflows projection remains in place for existing clients.
This module is authoritative for template, version, instance and attempts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import uuid
from typing import Any

from .api_errors import APIError
from .kv_store_protocol import KVStore

TEMPLATE_PREFIX = "jobflows/templates"
INSTANCE_PREFIX = "jobflows/instances"
IDEMPOTENCY_PREFIX = "jobflows/idempotency"
TERMINAL_INSTANCE_STATES = {"SUCCEEDED", "FAILED", "ABORTED", "SUPERSEDED"}
DELETABLE_INSTANCE_STATES = TERMINAL_INSTANCE_STATES | {"QUEUED", "ARCHIVED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _id(value: Any, prefix: str) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        candidate = f"{prefix}-{uuid.uuid4().hex[:12]}"
    if "/" in candidate or chr(92) in candidate:
        raise APIError("INVALID_ID", "标识不能包含路径分隔符", 422)
    return candidate


class JobFlowService:
    """KV-backed domain service with immutable version boundaries."""

    def __init__(self, store: KVStore):
        self.store = store

    def _read(self, key: str, default: Any = None) -> Any:
        raw, _ = self.store.kv_get(key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return raw

    def _write(self, key: str, value: Any) -> None:
        self.store.kv_put(key, _json(value) if not isinstance(value, str) else value)

    def _template(self, template_id: str) -> dict:
        value = self._read(f"{TEMPLATE_PREFIX}/{template_id}/meta")
        if not isinstance(value, dict):
            raise APIError("TEMPLATE_NOT_FOUND", "作业流模板不存在", 404)
        return value

    def _instance(self, instance_id: str) -> dict:
        value = self._read(f"{INSTANCE_PREFIX}/{instance_id}/meta")
        if not isinstance(value, dict):
            raise APIError("INSTANCE_NOT_FOUND", "作业流实例不存在", 404)
        return value

    def _revision(self, key: str) -> int:
        value = self._read(key, 0)
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _check_revision(self, key: str, expected: Any) -> int:
        current = self._revision(key)
        if expected is not None and int(expected) != current:
            raise APIError("REVISION_CONFLICT", "资源已被其他用户修改，请刷新后重试", 409, {
                "expected_revision": int(expected), "actual_revision": current,
            })
        return current

    def _manifest(self, body: dict) -> dict:
        source = body.get("manifest")
        manifest = dict(source) if isinstance(source, dict) else {}
        tasks = manifest.get("tasks")
        if tasks is None:
            tasks = body.get("tasks", body.get("nodes"))
        if isinstance(tasks, dict):
            # Accept the project-native DAG notation:
            # {"design": {"type": "design", ...}, "backend": {...}}.
            normalized_tasks = []
            for task_id, task in tasks.items():
                if not isinstance(task, dict):
                    raise APIError("INVALID_TASK", "作业流节点必须是对象", 422)
                normalized_tasks.append({"id": task_id, **task})
            tasks = normalized_tasks
        if not isinstance(tasks, list) or not tasks:
            raise APIError("TASKS_REQUIRED", "至少需要一个作业流节点", 422)
        normalized: list[dict] = []
        ids: set[str] = set()
        for raw in tasks:
            if not isinstance(raw, dict):
                raise APIError("INVALID_TASK", "作业流节点必须是对象", 422)
            task_id = _id(raw.get("id") or raw.get("task_id"), "task")
            if task_id in ids:
                raise APIError("DUPLICATE_TASK_ID", f"节点 ID 重复: {task_id}", 422)
            ids.add(task_id)
            depends = raw.get("depends_on", raw.get("dependsOn", []))
            if not isinstance(depends, list):
                raise APIError("INVALID_DEPENDENCIES", f"节点 {task_id} 的依赖必须是数组", 422)
            item = dict(raw)
            item["id"] = task_id
            item["depends_on"] = [str(item).strip() for item in depends if str(item).strip()]
            item.pop("dependsOn", None)
            normalized.append(item)
        known = {item["id"] for item in normalized}
        unknown = sorted({
            dep for item in normalized for dep in item["depends_on"] if dep not in known
        })
        if unknown:
            raise APIError("UNKNOWN_DEPENDENCY", "依赖节点不存在", 422, {"tasks": unknown})
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise APIError("TASK_GRAPH_CYCLE", "作业流依赖不能形成环", 422)
            if task_id in visited:
                return
            visiting.add(task_id)
            item = next(node for node in normalized if node["id"] == task_id)
            for dependency in item["depends_on"]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task in normalized:
            visit(task["id"])
        manifest["tasks"] = normalized
        manifest["parameters"] = manifest.get("parameters", body.get("parameters", {}))
        manifest["manifest_hash"] = _hash({
            key: value for key, value in manifest.items() if key != "manifest_hash"
        })
        return manifest

    def _template_view(self, meta: dict) -> dict:
        template_id = meta["template_id"]
        draft = self._read(f"{TEMPLATE_PREFIX}/{template_id}/draft/current", {})
        return {**meta, "draft": draft, "draft_revision": self._revision(
            f"{TEMPLATE_PREFIX}/{template_id}/draft/revision"
        )}

    def _append_event(self, base: str, event_type: str, actor: str, data: dict) -> dict:
        event_id = f"event-{uuid.uuid4().hex[:12]}"
        event = {"event_id": event_id, "type": event_type, "actor": actor,
                 "created_at": _now(), "data": data}
        self._write(f"{base}/events/{event_id}", event)
        return event

    def _idempotent(self, key: str | None) -> dict | None:
        if not key:
            return None
        value = self._read(f"{IDEMPOTENCY_PREFIX}/{key}")
        return value if isinstance(value, dict) else None

    def _remember(self, key: str | None, response: dict) -> None:
        if key:
            self._write(f"{IDEMPOTENCY_PREFIX}/{key}", response)

    def list_templates(self, group_id: str | None = None) -> list[dict]:
        items, _ = self.store.kv_get(f"{TEMPLATE_PREFIX}/", recurse=True)
        result: dict[str, dict] = {}
        for item in items or []:
            key = item.get("Key", "")
            if len(key.split("/")) == 4 and key.endswith("/meta"):
                value = self._read(key)
                if isinstance(value, dict) and (not group_id or value.get("group_id") == group_id):
                    result[value["template_id"]] = self._template_view(value)
        return sorted(result.values(), key=lambda value: value.get("updated_at", ""), reverse=True)

    def create_template(self, body: dict, actor: str) -> dict:
        template_id = _id(body.get("template_id") or body.get("id"), "tpl")
        if self._read(f"{TEMPLATE_PREFIX}/{template_id}/meta") is not None:
            raise APIError("TEMPLATE_ALREADY_EXISTS", "模板已存在", 409, {"template_id": template_id})
        manifest = self._manifest(body)
        now = _now()
        meta = {
            "template_id": template_id,
            "name": str(body.get("name") or body.get("title") or template_id).strip(),
            "description": str(body.get("description") or body.get("requirement") or "").strip(),
            "group_id": str(body.get("group_id") or "unassigned"),
            "status": "ACTIVE", "current_version_id": None,
            "created_by": actor, "created_at": now, "updated_at": now,
        }
        self._write(f"{TEMPLATE_PREFIX}/{template_id}/meta", meta)
        self._write(f"{TEMPLATE_PREFIX}/{template_id}/draft/current", manifest)
        self._write(f"{TEMPLATE_PREFIX}/{template_id}/draft/revision", 1)
        self._write(f"{TEMPLATE_PREFIX}/{template_id}/versions/index", [])
        self._append_event(f"{TEMPLATE_PREFIX}/{template_id}", "TEMPLATE_CREATED", actor, meta)
        return self._template_view(meta)

    def get_template(self, template_id: str) -> dict:
        return self._template_view(self._template(template_id))

    def delete_template(self, template_id: str, actor: str) -> dict:
        """Hard-delete a template only when it has never had an instance.

        Instance records retain a reference to both the template and the
        immutable version they executed. Even archived/superseded instances
        therefore block deletion so historical execution data stays
        addressable.
        """
        template = self._template(template_id)
        instances = self.list_instances(template_id)
        if instances:
            raise APIError(
                "TEMPLATE_HAS_INSTANCES",
                "模板已绑定作业流实例，不能删除；请归档模板",
                409,
                {"template_id": template_id,
                 "instance_ids": [item["instance_id"] for item in instances]},
            )
        self.store.kv_delete(f"{TEMPLATE_PREFIX}/{template_id}", recurse=True)
        return {"deleted": True, "template_id": template["template_id"], "deleted_by": actor}

    def update_draft(self, template_id: str, body: dict, actor: str) -> dict:
        meta = self._template(template_id)
        revision_key = f"{TEMPLATE_PREFIX}/{template_id}/draft/revision"
        self._check_revision(revision_key, body.get("expected_revision"))
        patch = dict(body)
        patch.pop("expected_revision", None)
        existing = self._read(f"{TEMPLATE_PREFIX}/{template_id}/draft/current", {})
        merged = dict(existing) if isinstance(existing, dict) else {}
        if "manifest" in patch or "tasks" in patch or "nodes" in patch:
            merged.update(patch)
            manifest = self._manifest(merged)
        else:
            manifest = existing
        if "name" in patch:
            meta["name"] = str(patch["name"]).strip() or meta["name"]
        if "description" in patch:
            meta["description"] = str(patch["description"]).strip()
        meta["updated_at"] = _now()
        self._write(f"{TEMPLATE_PREFIX}/{template_id}/meta", meta)
        self._write(f"{TEMPLATE_PREFIX}/{template_id}/draft/current", manifest)
        self._write(revision_key, self._revision(revision_key) + 1)
        self._append_event(f"{TEMPLATE_PREFIX}/{template_id}", "DRAFT_UPDATED", actor, {
            "revision": self._revision(revision_key),
        })
        return self._template_view(meta)

    def validate_template(self, template_id: str) -> dict:
        self._template(template_id)
        draft = self._read(f"{TEMPLATE_PREFIX}/{template_id}/draft/current", {})
        try:
            manifest = self._manifest(draft)
        except APIError as error:
            result = {"valid": False, "errors": [{"code": error.code, "message": error.message}]}
            self._write(f"{TEMPLATE_PREFIX}/{template_id}/draft/validation", result)
            return result
        result = {"valid": True, "errors": [], "manifest_hash": manifest["manifest_hash"],
                  "task_count": len(manifest["tasks"])}
        self._write(f"{TEMPLATE_PREFIX}/{template_id}/draft/current", manifest)
        self._write(f"{TEMPLATE_PREFIX}/{template_id}/draft/validation", result)
        return result

    def list_versions(self, template_id: str) -> list[dict]:
        self._template(template_id)
        return self._read(f"{TEMPLATE_PREFIX}/{template_id}/versions/index", []) or []

    def publish_template(self, template_id: str, body: dict, actor: str, key: str | None) -> dict:
        cached = self._idempotent(key)
        if cached:
            return cached
        meta = self._template(template_id)
        revision_key = f"{TEMPLATE_PREFIX}/{template_id}/draft/revision"
        self._check_revision(revision_key, body.get("expected_revision"))
        manifest = self._manifest(self._read(f"{TEMPLATE_PREFIX}/{template_id}/draft/current", {}))
        versions = self.list_versions(template_id)
        number = len(versions) + 1
        version_id = f"v{number}-{uuid.uuid4().hex[:8]}"
        version = {
            "version_id": version_id, "template_id": template_id, "version": number,
            "status": "PUBLISHED", "manifest_hash": manifest["manifest_hash"],
            "created_by": actor, "created_at": _now(),
            "release_note": str(body.get("release_note") or "").strip(),
        }
        self._write(f"{TEMPLATE_PREFIX}/{template_id}/versions/{version_id}/manifest", manifest)
        self._write(f"{TEMPLATE_PREFIX}/{template_id}/versions/{version_id}/status", version)
        versions.append(version)
        self._write(f"{TEMPLATE_PREFIX}/{template_id}/versions/index", versions)
        meta["current_version_id"] = version_id
        meta["updated_at"] = _now()
        self._write(f"{TEMPLATE_PREFIX}/{template_id}/meta", meta)
        self._append_event(f"{TEMPLATE_PREFIX}/{template_id}", "VERSION_PUBLISHED", actor, version)
        response = {"template": self._template_view(meta), "version": version}
        self._remember(key, response)
        return response

    def set_version_status(self, template_id: str, version_id: str, status: str, actor: str) -> dict:
        self._template(template_id)
        versions = self.list_versions(template_id)
        version = next((value for value in versions if value["version_id"] == version_id), None)
        if version is None:
            raise APIError("VERSION_NOT_FOUND", "模板版本不存在", 404)
        if status not in {"DISABLED", "ARCHIVED"}:
            raise APIError("INVALID_VERSION_STATUS", "不支持的版本状态", 422)
        version["status"] = status
        self._write(f"{TEMPLATE_PREFIX}/{template_id}/versions/{version_id}/status", version)
        self._write(f"{TEMPLATE_PREFIX}/{template_id}/versions/index", versions)
        self._append_event(f"{TEMPLATE_PREFIX}/{template_id}", "VERSION_STATUS_CHANGED", actor, version)
        return version

    def _version_manifest(self, template_id: str, version_id: str | None) -> tuple[dict, dict]:
        meta = self._template(template_id)
        version_id = version_id or meta.get("current_version_id")
        if not version_id:
            raise APIError("VERSION_REQUIRED", "模板尚未发布版本", 422)
        version = next((item for item in self.list_versions(template_id) if item["version_id"] == version_id), None)
        if not version:
            raise APIError("VERSION_NOT_FOUND", "模板版本不存在", 404)
        if version["status"] != "PUBLISHED":
            raise APIError("VERSION_NOT_AVAILABLE", "只能从已发布版本创建实例", 422)
        return version, self._read(f"{TEMPLATE_PREFIX}/{template_id}/versions/{version_id}/manifest")

    def list_instances(
        self, template_id: str | None = None, group_id: str | None = None,
    ) -> list[dict]:
        items, _ = self.store.kv_get(f"{INSTANCE_PREFIX}/", recurse=True)
        result: dict[str, dict] = {}
        for item in items or []:
            key = item.get("Key", "")
            if not key.endswith("/meta"):
                continue
            value = self._read(key)
            if not isinstance(value, dict):
                continue
            if template_id and value.get("template_id") != template_id:
                continue
            if group_id:
                template_meta = self._read(
                    f"{TEMPLATE_PREFIX}/{value.get('template_id')}/meta", {}
                )
                if not isinstance(template_meta, dict) or template_meta.get("group_id") != group_id:
                    continue
            else:
                template_meta = self._read(
                    f"{TEMPLATE_PREFIX}/{value.get('template_id')}/meta", {}
                )
            inherited_group_id = (
                template_meta.get("group_id")
                if isinstance(template_meta, dict) else None
            )
            instance_id = value["instance_id"]
            result[instance_id] = {
                **value,
                "group_id": inherited_group_id,
                "status": self._read(f"{INSTANCE_PREFIX}/{instance_id}/status", {}),
            }
        return sorted(result.values(), key=lambda value: value.get("created_at", ""), reverse=True)

    def _instance_view(self, meta: dict) -> dict:
        instance_id = meta["instance_id"]
        template_meta = self._read(
            f"{TEMPLATE_PREFIX}/{meta.get('template_id')}/meta", {}
        )
        context = self._read(f"{INSTANCE_PREFIX}/{instance_id}/context", {})
        status = self._read(f"{INSTANCE_PREFIX}/{instance_id}/status", {})
        tasks_raw, _ = self.store.kv_get(f"{INSTANCE_PREFIX}/{instance_id}/tasks/", recurse=True)
        tasks: dict[str, dict] = {}
        for item in tasks_raw or []:
            key = item.get("Key", "")
            if key.endswith("/status"):
                value = self._read(key)
                task_id = key.split("/")[-2]
                if isinstance(value, dict):
                    tasks[task_id] = value
        return {
            **meta,
            "group_id": template_meta.get("group_id") if isinstance(template_meta, dict) else None,
            "context": context,
            "status": status,
            "tasks": tasks,
        }

    def create_instance(self, body: dict, actor: str, key: str | None) -> dict:
        cached = self._idempotent(key)
        if cached:
            return cached
        template_id = _id(body.get("template_id"), "tpl")
        template = self._template(template_id)
        requested_group_id = str(body.get("group_id") or "").strip()
        if requested_group_id and requested_group_id != template.get("group_id"):
            raise APIError(
                "GROUP_SCOPE_MISMATCH",
                "作业流实例必须继承模板所属项目组",
                422,
                {
                    "template_group_id": template.get("group_id"),
                    "requested_group_id": requested_group_id,
                },
            )
        version, manifest = self._version_manifest(template_id, body.get("version_id"))
        instance_id = _id(body.get("instance_id"), "inst")
        if self._read(f"{INSTANCE_PREFIX}/{instance_id}/meta") is not None:
            raise APIError("INSTANCE_ALREADY_EXISTS", "实例已存在", 409, {"instance_id": instance_id})
        now = _now()
        context = {
            "parameters": body.get("parameters", {}),
            "git": body.get("git", {"ref": body.get("git_ref")}),
            "workspace": body.get("workspace", {}),
            "execution_policy": body.get("execution_policy", {}),
        }
        meta = {
            "instance_id": instance_id, "template_id": template_id,
            "version_id": version["version_id"], "definition_hash": version["manifest_hash"],
            "name": str(body.get("name") or instance_id).strip(),
            "created_by": actor, "created_at": now, "updated_at": now,
            "successor_of": body.get("successor_of"),
        }
        status = {"state": "QUEUED", "revision": 1, "updated_at": now}
        self._write(f"{INSTANCE_PREFIX}/{instance_id}/meta", meta)
        self._write(f"{INSTANCE_PREFIX}/{instance_id}/context", context)
        self._write(f"{INSTANCE_PREFIX}/{instance_id}/status", status)
        self._write(f"{INSTANCE_PREFIX}/{instance_id}/status_revision", 1)
        for task in manifest["tasks"]:
            task_id = task["id"]
            task_status = "PENDING" if not task["depends_on"] else "BLOCKED"
            self._write(f"{INSTANCE_PREFIX}/{instance_id}/tasks/{task_id}/definition", task)
            self._write(f"{INSTANCE_PREFIX}/{instance_id}/tasks/{task_id}/status", {
                "task_id": task_id, "state": task_status, "attempt_count": 0,
            })
        self._append_event(f"{INSTANCE_PREFIX}/{instance_id}", "INSTANCE_CREATED", actor, meta)
        response = {"instance": self._instance_view(meta)}
        self._remember(key, response)
        return response

    def get_instance(self, instance_id: str) -> dict:
        return self._instance_view(self._instance(instance_id))

    def _transition(self, instance_id: str, state: str, actor: str) -> dict:
        meta = self._instance(instance_id)
        status_key = f"{INSTANCE_PREFIX}/{instance_id}/status"
        current = self._read(status_key, {})
        current_state = current.get("state", "QUEUED")
        allowed = {
            "RUNNING": {"QUEUED", "PAUSED"},
            "PAUSED": {"RUNNING"},
            "DRAINING": {"QUEUED", "RUNNING", "PAUSED"},
            "ABORTED": {"QUEUED", "RUNNING", "PAUSED", "DRAINING"},
            "ARCHIVED": TERMINAL_INSTANCE_STATES,
        }
        if state not in allowed or current_state not in allowed[state]:
            raise APIError("INVALID_INSTANCE_TRANSITION", f"实例不能从 {current_state} 转为 {state}", 409)
        revision = int(current.get("revision", 0)) + 1
        updated = {"state": state, "revision": revision, "updated_at": _now()}
        self._write(status_key, updated)
        self._write(f"{INSTANCE_PREFIX}/{instance_id}/status_revision", revision)
        meta["updated_at"] = updated["updated_at"]
        self._write(f"{INSTANCE_PREFIX}/{instance_id}/meta", meta)
        self._append_event(f"{INSTANCE_PREFIX}/{instance_id}", "INSTANCE_STATUS_CHANGED", actor, updated)
        return self._instance_view(meta)

    def start_instance(self, instance_id: str, actor: str) -> dict:
        instance = self._transition(instance_id, "RUNNING", actor)
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        self._write(f"{INSTANCE_PREFIX}/{instance_id}/run/current", {
            "run_id": run_id, "status": "RUNNING", "started_at": _now(),
        })
        return {"instance": instance, "run": self._read(
            f"{INSTANCE_PREFIX}/{instance_id}/run/current"
        )}

    def control_instance(self, instance_id: str, action: str, actor: str) -> dict:
        transitions = {"pause": "PAUSED", "abort": "ABORTED", "drain": "DRAINING"}
        if action not in transitions:
            raise APIError("INVALID_INSTANCE_ACTION", "不支持的实例操作", 422)
        return {"instance": self._transition(instance_id, transitions[action], actor)}

    def archive_instance(self, instance_id: str, actor: str) -> dict:
        return {"instance": self._transition(instance_id, "ARCHIVED", actor)}

    def delete_instance(self, instance_id: str, actor: str) -> dict:
        """Hard-delete an instance only after it has reached a safe state."""
        meta = self._instance(instance_id)
        status = self._read(f"{INSTANCE_PREFIX}/{instance_id}/status", {})
        state = status.get("state", "QUEUED")
        if state not in DELETABLE_INSTANCE_STATES:
            raise APIError(
                "INSTANCE_NOT_DELETABLE",
                "已开始运行的作业流实例不能删除，请先中止或等待进入终态",
                409,
                {"instance_id": instance_id, "state": state},
            )
        self.store.kv_delete(f"{INSTANCE_PREFIX}/{instance_id}", recurse=True)
        return {
            "deleted": True,
            "instance_id": instance_id,
            "template_id": meta.get("template_id"),
            "deleted_by": actor,
        }

    def rerun_instance(self, instance_id: str, body: dict, actor: str, key: str | None) -> dict:
        cached = self._idempotent(key)
        if cached:
            return cached
        source = self.get_instance(instance_id)
        context = dict(source["context"])
        for field in ("parameters", "git", "workspace", "execution_policy"):
            if field in body:
                context[field] = body[field]
        request = {
            "template_id": source["template_id"], "version_id": source["version_id"],
            "instance_id": body.get("instance_id"), "name": body.get("name") or f"{source['name']} (rerun)",
            "parameters": context["parameters"], "git": context["git"],
            "workspace": context["workspace"], "execution_policy": context["execution_policy"],
            "successor_of": instance_id,
        }
        response = self.create_instance(request, actor, None)
        successor_id = response["instance"]["instance_id"]
        self._transition(instance_id, "DRAINING", actor)
        if body.get("start", True):
            response = self.start_instance(successor_id, actor)
        self._write(f"{INSTANCE_PREFIX}/{instance_id}/successor", {"instance_id": successor_id})
        response["successor_of"] = instance_id
        self._remember(key, response)
        return response

    def retry_task(self, instance_id: str, task_id: str, actor: str) -> dict:
        self._instance(instance_id)
        status_key = f"{INSTANCE_PREFIX}/{instance_id}/tasks/{task_id}/status"
        current = self._read(status_key)
        if not isinstance(current, dict):
            raise APIError("TASK_NOT_FOUND", "实例任务不存在", 404)
        attempt_count = int(current.get("attempt_count", 0)) + 1
        attempt_id = f"attempt-{attempt_count}-{uuid.uuid4().hex[:8]}"
        current.update({"state": "READY", "attempt_count": attempt_count, "current_attempt": attempt_id})
        self._write(status_key, current)
        self._write(f"{INSTANCE_PREFIX}/{instance_id}/tasks/{task_id}/attempts/{attempt_id}", {
            "attempt_id": attempt_id, "state": "READY", "created_at": _now(), "created_by": actor,
        })
        return {"instance": self.get_instance(instance_id), "attempt_id": attempt_id}

    def create_changeset(self, instance_id: str, body: dict, actor: str) -> dict:
        instance = self.get_instance(instance_id)
        changeset_id = f"change-{uuid.uuid4().hex[:12]}"
        changed = body.get("changed_tasks", body.get("tasks", []))
        if not isinstance(changed, list):
            changed = []
        changed_ids = {str(item.get("id") if isinstance(item, dict) else item) for item in changed}
        definitions = {
            task_id: self._read(f"{INSTANCE_PREFIX}/{instance_id}/tasks/{task_id}/definition", {})
            for task_id in instance["tasks"]
        }
        impacted = set(changed_ids)
        changed_flag = True
        while changed_flag:
            changed_flag = False
            for task_id, definition in definitions.items():
                if task_id not in impacted and any(
                    dep in impacted for dep in definition.get("depends_on", [])
                ):
                    impacted.add(task_id)
                    changed_flag = True
        change = {
            "changeset_id": changeset_id, "instance_id": instance_id,
            "state": "IMPACT_ANALYZED", "created_by": actor, "created_at": _now(),
            "changed_tasks": sorted(changed_ids), "impacted_tasks": sorted(impacted),
            "changes": body.get("changes", {}),
            "reuse": {task_id: False for task_id in sorted(impacted)},
        }
        self._write(f"{INSTANCE_PREFIX}/{instance_id}/changesets/{changeset_id}", change)
        return {"changeset": change}

    def apply_changeset(self, instance_id: str, changeset_id: str, body: dict, actor: str, key: str | None) -> dict:
        change = self._read(f"{INSTANCE_PREFIX}/{instance_id}/changesets/{changeset_id}")
        if not isinstance(change, dict):
            raise APIError("CHANGESET_NOT_FOUND", "变更集不存在", 404)
        response = self.rerun_instance(instance_id, body, actor, key)
        change["state"] = "APPLIED"
        change["applied_at"] = _now()
        change["successor_instance_id"] = response["instance"]["instance_id"]
        self._write(f"{INSTANCE_PREFIX}/{instance_id}/changesets/{changeset_id}", change)
        response["changeset"] = change
        return response

    def list_events(self, instance_id: str) -> list[dict]:
        self._instance(instance_id)
        items, _ = self.store.kv_get(f"{INSTANCE_PREFIX}/{instance_id}/events/", recurse=True)
        events = [self._read(item["Key"]) for item in items or []]
        return sorted(
            (item for item in events if isinstance(item, dict)),
            key=lambda item: item.get("created_at", ""),
        )
