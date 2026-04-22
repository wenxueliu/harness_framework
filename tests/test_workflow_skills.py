"""
WorkflowSkills 单元测试
"""
from __future__ import annotations

import base64
import json
import time
from unittest.mock import MagicMock, Mock

import pytest

from harness_framework.workflow_skills import WorkflowSkills


def _make_store(initial: dict) -> tuple[MagicMock, dict]:
    """构建适配 WorkflowSkills 逻辑的 mock ConsulClient。返回 (consul, store)"""
    store = dict(initial)
    indices = {k: 1 for k in store}
    _store = store

    def kv_get(key: str, recurse: bool = False, cas: bool = False):
        if recurse:
            prefix = key.rstrip("/") + "/"
            matches = []
            for k, v in _store.items():
                if k.startswith(prefix):
                    matches.append({
                        "Key": k,
                        "Value": base64.b64encode(v.encode()).decode() if v else "",
                        "ModifyIndex": indices[k],
                        "_decoded": v,
                    })
            return matches if matches else None, 0

        v = _store.get(key)
        return v, indices.get(key, 0)

    def kv_put(key: str, value: str, cas: int = None) -> bool:
        current_idx = indices.get(key, 0)
        if cas is not None and current_idx != cas:
            return False
        _store[key] = value
        indices[key] = indices.get(key, 0) + 1
        return True

    def kv_delete(key: str, recurse: bool = False):
        if recurse:
            prefix = key.rstrip("/") + "/"
            to_del = [k for k in list(_store.keys()) if k.startswith(prefix)]
            for k in to_del:
                del _store[k]
        elif key in _store:
            del _store[key]

    consul = MagicMock()
    consul.kv_get = Mock(side_effect=kv_get)
    consul.kv_put = Mock(side_effect=kv_put)
    consul.kv_delete = Mock(side_effect=kv_delete)
    return consul, _store


class TestWorkflowSkills:
    def test_check_workflow_status_returns_default_when_empty(self):
        """status 不存在时返回 DRAFT"""
        consul, _ = _make_store({})
        skills = WorkflowSkills(consul)

        status = skills.check_workflow_status("req-001")

        assert status == "DRAFT"

    def test_check_workflow_status_returns_existing_value(self):
        """status 存在时返回实际值"""
        consul, _ = _make_store({"workflows/req-001/status": "CONFIRMED"})
        skills = WorkflowSkills(consul)

        status = skills.check_workflow_status("req-001")

        assert status == "CONFIRMED"

    def test_wait_for_proposal_resolves_immediately(self):
        """状态不是 Proposal 时立即返回"""
        consul, _ = _make_store({"workflows/req-001/status": "CONFIRMED"})
        skills = WorkflowSkills(consul)

        result = skills.wait_for_proposal("req-001", timeout=60, poll_interval=1)

        assert result["resolved"] is True
        assert result["status"] == "CONFIRMED"

    def test_wait_for_proposal_resolves_when_proposal_confirmed(self):
        """等待过程中 Proposal 被确认"""
        consul, store = _make_store({"workflows/req-001/status": "Proposal"})
        skills = WorkflowSkills(consul)

        def update_status():
            time.sleep(0.1)
            store["workflows/req-001/status"] = "CONFIRMED"

        import threading
        t = threading.Thread(target=update_status)
        t.start()

        result = skills.wait_for_proposal("req-001", timeout=5, poll_interval=0.05)
        t.join()

        assert result["resolved"] is True
        assert result["status"] == "CONFIRMED"

    def test_wait_for_proposal_timeout(self):
        """等待超时"""
        consul, _ = _make_store({"workflows/req-001/status": "Proposal"})
        skills = WorkflowSkills(consul)

        start = time.time()
        result = skills.wait_for_proposal("req-001", timeout=1, poll_interval=0.1)
        elapsed = time.time() - start

        assert result["resolved"] is False
        assert result["reason"] == "timeout"
        assert elapsed >= 1

    def test_propose_task_creates_task_and_sets_proposal(self):
        """提出新任务，设置 status 为 Proposal"""
        consul, store = _make_store({
            "workflows/req-001/status": "IN_PROGRESS",
            "workflows/req-001/dependencies": json.dumps({
                "design": {"type": "design"},
            }),
        })
        skills = WorkflowSkills(consul)

        result = skills.propose_task(
            "req-001",
            "perf-opt",
            {"type": "task", "depends_on": ["design"], "proposed_by": "test"},
        )

        assert result["success"] is True
        assert result["status"] == "Proposal"
        assert result["already_proposed"] is False

        deps = json.loads(store["workflows/req-001/dependencies"])
        assert "perf-opt" in deps
        assert store["workflows/req-001/tasks/perf-opt/proposed_by"] == "test"
        assert store["workflows/req-001/tasks/perf-opt/proposed_at"] is not None

    def test_propose_task_concurrent_proposal_returns_already_proposed(self):
        """并发提出时，第二个返回 already_proposed"""
        consul, store = _make_store({
            "workflows/req-001/status": "Proposal",
            "workflows/req-001/dependencies": json.dumps({}),
        })
        skills = WorkflowSkills(consul)

        result = skills.propose_task(
            "req-001",
            "perf-opt",
            {"type": "task", "depends_on": ["design"], "proposed_by": "test"},
        )

        assert result["success"] is True
        assert result["already_proposed"] is True
        assert store["workflows/req-001/status"] == "Proposal"

    def test_propose_task_idempotent_when_task_exists(self):
        """任务已存在时返回失败"""
        consul, store = _make_store({
            "workflows/req-001/status": "CONFIRMED",
            "workflows/req-001/dependencies": json.dumps({
                "perf-opt": {"type": "task"},
            }),
        })
        skills = WorkflowSkills(consul)

        result = skills.propose_task(
            "req-001",
            "perf-opt",
            {"type": "task", "proposed_by": "test"},
        )

        assert result["success"] is False
        assert result["reason"] == "task already exists"

    def test_propose_task_force_updates_existing(self):
        """force=True 时覆盖已存在的任务"""
        consul, store = _make_store({
            "workflows/req-001/status": "CONFIRMED",
            "workflows/req-001/dependencies": json.dumps({
                "perf-opt": {"type": "task", "old_field": "value"},
            }),
        })
        skills = WorkflowSkills(consul)

        result = skills.propose_task(
            "req-001",
            "perf-opt",
            {"type": "task", "new_field": "value2", "proposed_by": "test"},
            force=True,
        )

        assert result["success"] is True

        deps = json.loads(store["workflows/req-001/dependencies"])
        assert "new_field" in deps["perf-opt"]
        assert "old_field" not in deps["perf-opt"]

    def test_list_pending_proposals_returns_proposed_tasks(self):
        """列出所有待确认的提案"""
        consul, store = _make_store({
            "workflows/req-001/status": "Proposal",
            "workflows/req-001/dependencies": json.dumps({
                "design": {"type": "design"},
                "perf-opt": {
                    "type": "task",
                    "depends_on": ["design"],
                    "proposed_by": "test",
                    "proposed_at": "2025-04-23T10:00:00Z",
                    "reason": "performance test failed",
                },
                "sec-fix": {
                    "type": "task",
                    "depends_on": ["design"],
                    "proposed_by": "test",
                },
            }),
        })
        skills = WorkflowSkills(consul)

        proposals = skills.list_pending_proposals("req-001")

        assert len(proposals) == 2

        perf_opt = next(p for p in proposals if p["task_name"] == "perf-opt")
        assert perf_opt["proposed_by"] == "test"
        assert perf_opt["reason"] == "performance test failed"

    def test_list_pending_proposals_returns_empty_when_none(self):
        """没有提案时返回空列表"""
        consul, store = _make_store({
            "workflows/req-001/status": "CONFIRMED",
            "workflows/req-001/dependencies": json.dumps({
                "design": {"type": "design"},
                "backend": {"type": "backend"},
            }),
        })
        skills = WorkflowSkills(consul)

        proposals = skills.list_pending_proposals("req-001")

        assert proposals == []

    def test_get_dependencies_returns_existing(self):
        """获取已存在的 dependencies"""
        consul, store = _make_store({
            "workflows/req-001/dependencies": json.dumps({
                "design": {"type": "design"},
            }),
        })
        skills = WorkflowSkills(consul)

        deps = skills.get_dependencies("req-001")

        assert "design" in deps

    def test_get_dependencies_returns_empty_dict(self):
        """dependencies 不存在时返回空字典"""
        consul, store = _make_store({})
        skills = WorkflowSkills(consul)

        deps = skills.get_dependencies("req-001")

        assert deps == {}

    def test_confirm_proposal_changes_status_to_confirmed(self):
        """确认后状态变为 CONFIRMED"""
        consul, store = _make_store({
            "workflows/req-001/status": "Proposal",
            "workflows/req-001/dependencies": json.dumps({
                "perf-opt": {"type": "task", "proposed_by": "test"},
            }),
        })
        skills = WorkflowSkills(consul)

        result = skills.confirm_proposal("req-001")

        assert result["success"] is True
        assert result["status"] == "CONFIRMED"
        assert store["workflows/req-001/status"] == "CONFIRMED"

    def test_confirm_proposal_with_rejected_tasks_removes_tasks(self):
        """确认时可以拒绝部分任务"""
        consul, store = _make_store({
            "workflows/req-001/status": "Proposal",
            "workflows/req-001/dependencies": json.dumps({
                "perf-opt": {"type": "task", "proposed_by": "test"},
                "sec-fix": {"type": "task", "proposed_by": "test"},
            }),
        })
        skills = WorkflowSkills(consul)

        result = skills.confirm_proposal(
            "req-001",
            rejected_tasks=["sec-fix"],
        )

        assert result["success"] is True

        deps = json.loads(store["workflows/req-001/dependencies"])
        assert "perf-opt" in deps
        assert "sec-fix" not in deps

    def test_confirm_proposal_rejects_when_not_in_proposal_state(self):
        """非 Proposal 状态时确认失败"""
        consul, store = _make_store({
            "workflows/req-001/status": "CONFIRMED",
            "workflows/req-001/dependencies": json.dumps({}),
        })
        skills = WorkflowSkills(consul)

        result = skills.confirm_proposal("req-001")

        assert result["success"] is False
        assert "not in proposal state" in result["reason"]

    def test_confirm_proposal_with_accepted_tasks_only(self):
        """可以只接受部分任务（当前实现接受除 rejected 外的所有任务）"""
        consul, store = _make_store({
            "workflows/req-001/status": "Proposal",
            "workflows/req-001/dependencies": json.dumps({
                "perf-opt": {"type": "task", "proposed_by": "test"},
                "sec-fix": {"type": "task", "proposed_by": "test"},
                "doc-task": {"type": "task", "proposed_by": "test"},
            }),
        })
        skills = WorkflowSkills(consul)

        result = skills.confirm_proposal(
            "req-001",
            accepted_tasks=["perf-opt", "sec-fix"],
        )

        assert result["success"] is True

        deps = json.loads(store["workflows/req-001/dependencies"])
        assert "perf-opt" in deps
        assert "sec-fix" in deps

    def test_reject_proposal_changes_status_to_confirmed(self):
        """拒绝后状态变为 CONFIRMED"""
        consul, store = _make_store({
            "workflows/req-001/status": "Proposal",
            "workflows/req-001/dependencies": json.dumps({
                "perf-opt": {"type": "task", "proposed_by": "test"},
            }),
        })
        skills = WorkflowSkills(consul)

        result = skills.reject_proposal("req-001")

        assert result["success"] is True
        assert result["status"] == "CONFIRMED"
        assert store["workflows/req-001/status"] == "CONFIRMED"

    def test_reject_proposal_rejects_when_not_in_proposal_state(self):
        """非 Proposal 状态时拒绝失败"""
        consul, store = _make_store({
            "workflows/req-001/status": "IN_PROGRESS",
            "workflows/req-001/dependencies": json.dumps({}),
        })
        skills = WorkflowSkills(consul)

        result = skills.reject_proposal("req-001")

        assert result["success"] is False
        assert "not in proposal state" in result["reason"]