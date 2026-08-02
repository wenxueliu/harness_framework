"""
WorkflowSkills — Agent 可调用的工作流相关 skill

提供检查需求状态、等待 Proposal、提出新任务等能力。
"""
from __future__ import annotations

import datetime
import json
import time
from typing import Optional

from .kv_store_protocol import KVStore


class WorkflowSkills:
    def __init__(self, consul: KVStore):
        self.consul = consul

    def check_workflow_status(self, req_id: str) -> str:
        """检查需求当前状态"""
        status, _ = self.consul.kv_get(f"workflows/{req_id}/status")
        return status or "DRAFT"

    def wait_for_proposal(
        self, req_id: str, timeout: int = 3600, poll_interval: int = 5
    ) -> dict:
        """等待 Proposal 被解决（人工确认或拒绝）

        Args:
            req_id: 需求 ID
            timeout: 超时时间（秒），默认 1 小时
            poll_interval: 轮询间隔（秒）

        Returns:
            {"resolved": True, "status": "CONFIRMED"}  # 或 {"resolved": False, "reason": "timeout"}
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, _ = self.consul.kv_get(f"workflows/{req_id}/status")
            if status != "Proposal":
                return {"resolved": True, "status": status}
            time.sleep(poll_interval)

        return {"resolved": False, "reason": "timeout"}

    def propose_task(
        self,
        req_id: str,
        task_name: str,
        task_def: dict,
        force: bool = False,
    ) -> dict:
        """提出新的子任务

        Args:
            req_id: 需求 ID
            task_name: 新任务名称
            task_def: 任务定义（type, depends_on, proposed_by 等）
            force: 是否强制（忽略 CAS 冲突）

        Returns:
            {"success": True, "status": "Proposal", "already_proposed": False}
            {"success": True, "status": "Proposal", "already_proposed": True}
            {"success": False, "reason": "..."}
        """
        deps_key = f"workflows/{req_id}/dependencies"
        deps_str, deps_idx = self.consul.kv_get(deps_key)
        deps = json.loads(deps_str) if deps_str else {}

        if task_name in deps and not force:
            return {"success": False, "reason": "task already exists"}

        proposed_at = _now_iso()
        task_def = dict(task_def)
        task_def.setdefault("proposed_by", "unknown")
        task_def["proposed_at"] = proposed_at
        candidate = dict(deps)
        candidate[task_name] = task_def
        validation_error = _validate_dag(candidate)
        if validation_error:
            return {"success": False, "reason": validation_error}

        current, idx = self.consul.kv_get(f"workflows/{req_id}/status")
        already_proposed = current == "Proposal"
        if not already_proposed:
            success = self.consul.kv_put(
                f"workflows/{req_id}/status", "Proposal", cas=idx
            )
            if not success and not force:
                return {"success": False, "reason": "concurrent proposal detected"}

        deps_written = self.consul.kv_put(
            deps_key, json.dumps(candidate), cas=deps_idx
        )
        if not deps_written and not force:
            return {"success": False, "reason": "concurrent DAG update detected"}
        if force and not deps_written:
            self.consul.kv_put(deps_key, json.dumps(candidate))

        self.consul.kv_put(
            f"workflows/{req_id}/tasks/{task_name}/proposed_by",
            task_def.get("proposed_by", "unknown"),
        )
        self.consul.kv_put(
            f"workflows/{req_id}/tasks/{task_name}/proposed_at",
            proposed_at,
        )

        return {
            "success": True,
            "status": "Proposal",
            "already_proposed": already_proposed,
        }

    def list_pending_proposals(self, req_id: str) -> list[dict]:
        """查看当前待确认的提案"""
        deps_str, _ = self.consul.kv_get(f"workflows/{req_id}/dependencies")
        deps = json.loads(deps_str) if deps_str else {}

        proposals = []
        for task_name, task_def in deps.items():
            if "proposed_by" in task_def or "proposed_at" in task_def:
                proposals.append({
                    "task_name": task_name,
                    "proposed_by": task_def.get("proposed_by"),
                    "proposed_at": task_def.get("proposed_at"),
                    "depends_on": task_def.get("depends_on", []),
                    "type": task_def.get("type", "task"),
                    "reason": task_def.get("reason", ""),
                })

        return proposals

    def get_dependencies(self, req_id: str) -> dict:
        """获取 dependencies"""
        deps_str, _ = self.consul.kv_get(f"workflows/{req_id}/dependencies")
        return json.loads(deps_str) if deps_str else {}

    def confirm_proposal(
        self,
        req_id: str,
        accepted_tasks: Optional[list[str]] = None,
        rejected_tasks: Optional[list[str]] = None,
    ) -> dict:
        """人工确认 Proposal

        Args:
            req_id: 需求 ID
            accepted_tasks: 接受的任务列表（None 表示全部接受）
            rejected_tasks: 拒绝的任务列表

        Returns:
            {"success": True, "status": "CONFIRMED"}
        """
        status, idx = self.consul.kv_get(f"workflows/{req_id}/status")
        if status != "Proposal":
            return {"success": False, "reason": f"not in proposal state: {status}"}

        if rejected_tasks:
            deps_str, _ = self.consul.kv_get(f"workflows/{req_id}/dependencies")
            deps = json.loads(deps_str) if deps_str else {}
            for task_name in rejected_tasks:
                rejected = deps.get(task_name)
                if rejected is not None:
                    self.consul.kv_put(
                        f"workflows/{req_id}/proposal_history/{task_name}/{_history_seq()}",
                        json.dumps({
                            "decision": "rejected",
                            "decided_at": _now_iso(),
                            "task": rejected,
                        }),
                    )
                deps.pop(task_name, None)
            self.consul.kv_put(
                f"workflows/{req_id}/dependencies", json.dumps(deps)
            )

        self.consul.kv_put(f"workflows/{req_id}/status", "CONFIRMED", cas=idx)
        return {"success": True, "status": "CONFIRMED"}

    def reject_proposal(self, req_id: str) -> dict:
        """人工拒绝 Proposal，恢复到 CONFIRMED 状态"""
        status, idx = self.consul.kv_get(f"workflows/{req_id}/status")
        if status != "Proposal":
            return {"success": False, "reason": f"not in proposal state: {status}"}

        self.consul.kv_put(f"workflows/{req_id}/status", "CONFIRMED", cas=idx)
        return {"success": True, "status": "CONFIRMED"}


def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def _history_seq() -> str:
    return f"{int(time.time() * 1000000):021d}"


def _dependency_name(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("task", ""))
    return str(value)


def _validate_dag(deps: dict) -> str:
    """验证引用完整性和环；返回空字符串表示合法。"""
    for task_name, task_def in deps.items():
        for raw_dep in task_def.get("depends_on", []):
            dependency = _dependency_name(raw_dep)
            if not dependency or dependency not in deps:
                return f"unknown dependency for {task_name}: {dependency}"

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_name: str) -> bool:
        if task_name in visiting:
            return False
        if task_name in visited:
            return True
        visiting.add(task_name)
        for raw_dep in deps[task_name].get("depends_on", []):
            if not visit(_dependency_name(raw_dep)):
                return False
        visiting.remove(task_name)
        visited.add(task_name)
        return True

    if not all(visit(task_name) for task_name in deps):
        return "dependency cycle detected"
    return ""
