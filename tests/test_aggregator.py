"""
Aggregator 单元测试

用例：
- activate_blocked_task: 依赖全部 DONE → 状态→PENDING
- keep_blocked_when_deps_pending: 部分依赖未完成 → 保持 BLOCKED
- activate_parallel_children: parallel 节点激活所有 children
- retest_when_feedback_fixed: test FAILED + 所有 feedback FIXED → 重置为 PENDING
- skip_retest_when_feedback_open: 有 feedback 未 FIXED → 不重置
- skip_retest_exceed_limit: retry_count >= 3 → 不重置
- abort_workflow: control=ABORT → 所有非终态任务→ABORTED
- pause_workflow: control=PAUSE → 不推进任务
"""
from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, Mock

import pytest

from harness_framework.aggregator import Aggregator


def _make_store(initial: dict) -> MagicMock:
    """构建适配 Aggregator 逻辑的 mock ConsulClient。"""
    def kv_get(key: str, recurse: bool = False):
        if recurse:
            prefix = key.rstrip("/") + "/"
            matches = []
            for k, v in initial.items():
                if k.startswith(prefix):
                    matches.append({
                        "Key": k,
                        "Value": base64.b64encode(v.encode()).decode() if v else "",
                        "ModifyIndex": 1,
                        "_decoded": v,
                    })
            if matches:
                return matches, 1
            return None, 0
        v = initial.get(key)
        if v is not None:
            return v, 1
        return None, 0

    consul = MagicMock()
    consul.kv_get = Mock(side_effect=kv_get)
    consul.kv_put = Mock()
    consul.kv_delete = Mock()
    return consul


class TestAggregator:
    def test_activate_blocked_task(self):
        """backend 依赖 design DONE → backend 应激活为 PENDING。"""
        store = {
            "workflows/req-001/published": "true",
            "workflows/req-001/dependencies": json.dumps({
                "design": {"type": "design", "depends_on": []},
                "backend": {"type": "backend", "depends_on": ["design"]},
            }),
            "workflows/req-001/tasks/design/status": "DONE",
            "workflows/req-001/tasks/backend/status": "BLOCKED",
        }
        consul = _make_store(store)
        agg = Aggregator(consul, poll_interval=1)

        agg._process_requirement("req-001")

        calls = consul.kv_put.call_args_list
        backend_pending = any(
            "backend" in str(c) and c[0][1] == "PENDING"
            for c in calls
        )
        assert backend_pending, f"Expected backend to be PENDING, calls: {calls}"

        activated_at_set = any(
            "backend" in str(c) and "activated_at" in str(c)
            for c in calls
        )
        assert activated_at_set, "Expected activated_at to be written"

    def test_keep_blocked_when_deps_pending(self):
        """design 仍在 IN_PROGRESS → backend 应保持 BLOCKED。"""
        store = {
            "workflows/req-001/published": "true",
            "workflows/req-001/dependencies": json.dumps({
                "design": {"type": "design", "depends_on": []},
                "backend": {"type": "backend", "depends_on": ["design"]},
            }),
            "workflows/req-001/tasks/design/status": "IN_PROGRESS",
            "workflows/req-001/tasks/backend/status": "BLOCKED",
        }
        consul = _make_store(store)
        agg = Aggregator(consul, poll_interval=1)

        agg._process_requirement("req-001")

        backend_pending = any(
            "backend" in str(c) and c[0][1] == "PENDING"
            for c in consul.kv_put.call_args_list
        )
        assert not backend_pending, "backend should stay BLOCKED"

    def test_retest_when_feedback_fixed(self):
        """test FAILED + 所有 feedback FIXED → 重置为 PENDING。"""
        store = {
            "workflows/req-001/published": "true",
            "workflows/req-001/dependencies": json.dumps({
                "test": {"type": "test", "depends_on": ["backend"]},
            }),
            "workflows/req-001/tasks/test/status": "FAILED",
            "workflows/req-001/tasks/test/retry_count": "1",
            "workflows/req-001/feedback/login/status": "FIXED",
            "workflows/req-001/feedback/login/reason": "password hash fixed",
        }
        consul = _make_store(store)
        agg = Aggregator(consul, poll_interval=1)

        agg._process_requirement("req-001")

        test_pending = any(
            "test" in str(c) and c[0][1] == "PENDING"
            for c in consul.kv_put.call_args_list
        )
        assert test_pending, "test should be reset to PENDING"

        retry_count_2 = any(
            "retry_count" in str(c) and c[0][1] == "2"
            for c in consul.kv_put.call_args_list
        )
        assert retry_count_2, "retry_count should be 2"

        assert consul.kv_delete.called, "feedback should be deleted"

    def test_skip_retest_when_feedback_open(self):
        """有 feedback 状态为 OPEN → 不应重置。"""
        store = {
            "workflows/req-001/published": "true",
            "workflows/req-001/dependencies": json.dumps({
                "test": {"type": "test", "depends_on": ["backend"]},
            }),
            "workflows/req-001/tasks/test/status": "FAILED",
            "workflows/req-001/feedback/login/status": "OPEN",
        }
        consul = _make_store(store)
        agg = Aggregator(consul, poll_interval=1)

        agg._process_requirement("req-001")

        test_pending = any(
            "test" in str(c) and c[0][1] == "PENDING"
            for c in consul.kv_put.call_args_list
        )
        assert not test_pending, "test should NOT be reset when feedback is OPEN"

    def test_skip_retest_exceed_limit(self):
        """retry_count >= 3 → 不应重置。"""
        store = {
            "workflows/req-001/published": "true",
            "workflows/req-001/dependencies": json.dumps({
                "test": {"type": "test", "depends_on": ["backend"]},
            }),
            "workflows/req-001/tasks/test/status": "FAILED",
            "workflows/req-001/tasks/test/retry_count": "3",
            "workflows/req-001/feedback/login/status": "FIXED",
        }
        consul = _make_store(store)
        agg = Aggregator(consul, poll_interval=1)

        agg._process_requirement("req-001")

        test_pending = any(
            "test" in str(c) and c[0][1] == "PENDING"
            for c in consul.kv_put.call_args_list
        )
        assert not test_pending, "test should NOT be reset when retry_count >= 3"

    def test_abort_workflow(self):
        """control=ABORT → 所有非终态任务→ABORTED。"""
        store = {
            "workflows/req-001/published": "true",
            "workflows/req-001/control": "ABORT",
            "workflows/req-001/dependencies": json.dumps({
                "design": {"type": "design"},
                "backend": {"type": "backend"},
                "test": {"type": "test"},
            }),
            "workflows/req-001/tasks/design/status": "IN_PROGRESS",
            "workflows/req-001/tasks/backend/status": "PENDING",
            "workflows/req-001/tasks/test/status": "BLOCKED",
        }
        consul = _make_store(store)
        agg = Aggregator(consul, poll_interval=1)

        agg._process_requirement("req-001")

        aborted_count = sum(
            1 for c in consul.kv_put.call_args_list
            if "status" in str(c) and c[0][1] == "ABORTED"
        )
        assert aborted_count == 3, f"Expected 3 ABORTED, got {aborted_count}"

    def test_pause_workflow(self):
        """control=PAUSE → 不推进任务。"""
        store = {
            "workflows/req-001/control": "PAUSE",
            "workflows/req-001/dependencies": json.dumps({
                "design": {"type": "design", "depends_on": []},
                "backend": {"type": "backend", "depends_on": ["design"]},
            }),
            "workflows/req-001/tasks/design/status": "DONE",
            "workflows/req-001/tasks/backend/status": "BLOCKED",
        }
        consul = _make_store(store)
        agg = Aggregator(consul, poll_interval=1)

        agg._process_requirement("req-001")

        backend_pending = any(
            "backend" in str(c) and c[0][1] == "PENDING"
            for c in consul.kv_put.call_args_list
        )
        assert not backend_pending, "backend should NOT activate when PAUSE"

    def test_activate_parallel_children(self):
        """parallel 节点依赖 DONE → 激活所有 children。"""
        store = {
            "workflows/req-001/published": "true",
            "workflows/req-001/dependencies": json.dumps({
                "design": {"type": "design", "depends_on": []},
                "parallel-group": {
                    "type": "parallel",
                    "depends_on": ["design"],
                    "children": ["backend", "test"]
                },
                "backend": {"type": "backend", "depends_on": []},
                "test": {"type": "test", "depends_on": []},
            }),
            "workflows/req-001/tasks/design/status": "DONE",
            "workflows/req-001/tasks/parallel-group/status": "BLOCKED",
            "workflows/req-001/tasks/backend/status": "BLOCKED",
            "workflows/req-001/tasks/test/status": "BLOCKED",
        }
        consul = _make_store(store)
        agg = Aggregator(consul, poll_interval=1)

        agg._process_requirement("req-001")

        backend_pending = any(
            "backend" in str(c) and c[0][1] == "PENDING"
            for c in consul.kv_put.call_args_list
        )
        test_pending = any(
            "test" in str(c) and c[0][1] == "PENDING"
            for c in consul.kv_put.call_args_list
        )
        assert backend_pending, "backend child should be activated"
        assert test_pending, "test child should be activated"

    def test_priority_ordering(self):
        """高 priority 需求应先处理。"""
        store = {
            "workflows/req-001/published": "true",
            "workflows/req-001/dependencies": json.dumps({
                "design": {"type": "design", "depends_on": []},
            }),
            "workflows/req-001/priority": "1",
            "workflows/req-001/tasks/design/status": "",
            "workflows/req-002/dependencies": json.dumps({
                "design": {"type": "design", "depends_on": []},
            }),
            "workflows/req-002/priority": "10",
            "workflows/req-002/tasks/design/status": "",
        }
        consul = _make_store(store)
        agg = Aggregator(consul, poll_interval=1)

        agg._tick()

        assert consul.kv_put.called
