"""
Watchdog 单元测试

用例：
- recover_dead_agent: Agent 不在 alive list → 任务→PENDING
- recover_timeout: started_at 超时 → 任务→PENDING
- fail_after_max_retry: retry_count >= max_retry → 任务→FAILED
- ignore_alive_agent: Agent 存活且未超时 → 无操作
"""
from __future__ import annotations

import base64
from unittest.mock import MagicMock, Mock

import pytest

from harness_framework.watchdog import Watchdog


def _make_store(initial: dict) -> MagicMock:
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


class TestWatchdog:
    def test_recover_dead_agent(self):
        """Agent 不在 alive list → 任务回滚为 PENDING。"""
        store = {
            "workflows/req-001/published": "true",
            "workflows/req-001/tasks/backend/status": "IN_PROGRESS",
            "workflows/req-001/tasks/backend/assigned_agent": "agent-002",
            "workflows/req-001/tasks/backend/started_at": "2025-04-22T10:00:00",
            "workflows/req-001/tasks/backend/retry_count": "1",
        }
        consul = _make_store(store)
        consul.list_services = Mock(return_value=[
            {"Service": {"ID": "agent-001"}, "Checks": [{"Status": "passing"}]}
        ])

        wd = Watchdog(consul, poll_interval=1, task_timeout_seconds=3600, max_retry=3)
        wd._tick()

        put_calls = consul.kv_put.call_args_list
        backend_pending = any(
            "backend" in str(c) and c[0][1] == "PENDING"
            for c in put_calls
        )
        assert backend_pending, f"Expected PENDING, calls: {put_calls}"

        retry_inc = any(
            "retry_count" in str(c) and c[0][1] == "2"
            for c in put_calls
        )
        assert retry_inc, "retry_count should be incremented to 2"

    def test_recover_timeout(self):
        """started_at 超过 task_timeout → 任务回滚为 PENDING。"""
        import datetime
        old_time = (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).isoformat() + "Z"
        store = {
            "workflows/req-001/published": "true",
            "workflows/req-001/tasks/backend/status": "IN_PROGRESS",
            "workflows/req-001/tasks/backend/assigned_agent": "agent-001",
            "workflows/req-001/tasks/backend/started_at": old_time,
            "workflows/req-001/tasks/backend/retry_count": "0",
        }
        consul = _make_store(store)
        consul.list_services = Mock(return_value=[
            {"Service": {"ID": "agent-001"}, "Checks": [{"Status": "passing"}]}
        ])

        wd = Watchdog(consul, poll_interval=1, task_timeout_seconds=3600, max_retry=3)
        wd._tick()

        backend_pending = any(
            "backend" in str(c) and c[0][1] == "PENDING"
            for c in consul.kv_put.call_args_list
        )
        assert backend_pending, "task should be reset due to timeout"

    def test_fail_after_max_retry(self):
        """retry_count >= max_retry → 任务→FAILED + 告警写入。"""
        store = {
            "workflows/req-001/published": "true",
            "workflows/req-001/tasks/backend/status": "IN_PROGRESS",
            "workflows/req-001/tasks/backend/assigned_agent": "dead-agent",
            "workflows/req-001/tasks/backend/started_at": "2025-04-22T10:00:00",
            "workflows/req-001/tasks/backend/retry_count": "2",
        }
        consul = _make_store(store)
        consul.list_services = Mock(return_value=[])

        wd = Watchdog(consul, poll_interval=1, task_timeout_seconds=3600, max_retry=3)
        wd._tick()

        put_calls = consul.kv_put.call_args_list
        backend_failed = any(
            "backend" in str(c) and c[0][1] == "FAILED"
            for c in put_calls
        )
        assert backend_failed, "task should be FAILED after max retries"

        alert_written = any(
            "alerts/req-001/backend" in str(c)
            for c in put_calls
        )
        assert alert_written, "alert should be written"

    def test_ignore_alive_agent(self):
        """Agent 存活且未超时 → 无操作。"""
        import datetime
        recent_time = datetime.datetime.utcnow().isoformat() + "Z"
        store = {
            "workflows/req-001/published": "true",
            "workflows/req-001/tasks/backend/status": "IN_PROGRESS",
            "workflows/req-001/tasks/backend/assigned_agent": "agent-001",
            "workflows/req-001/tasks/backend/started_at": recent_time,
            "workflows/req-001/tasks/backend/retry_count": "0",
        }
        consul = _make_store(store)
        consul.list_services = Mock(return_value=[
            {"Service": {"ID": "agent-001"}, "Checks": [{"Status": "passing"}]}
        ])

        wd = Watchdog(consul, poll_interval=1, task_timeout_seconds=3600, max_retry=3)
        wd._tick()

        assert not consul.kv_put.called, "No action should be taken for healthy task"

    def test_skip_non_in_progress_tasks(self):
        """非 IN_PROGRESS 状态的任务应被跳过。"""
        store = {
            "workflows/req-001/published": "true",
            "workflows/req-001/tasks/design/status": "DONE",
            "workflows/req-001/tasks/design/assigned_agent": "agent-001",
            "workflows/req-001/tasks/design/started_at": "2025-04-22T08:00:00",
        }
        consul = _make_store(store)
        consul.list_services = Mock(return_value=[])

        wd = Watchdog(consul, poll_interval=1, task_timeout_seconds=3600, max_retry=3)
        wd._tick()

        assert not consul.kv_put.called, "DONE tasks should be skipped"

    def test_record_recovery_reason(self):
        """恢复时应记录原因和时间。"""
        store = {
            "workflows/req-001/published": "true",
            "workflows/req-001/tasks/backend/status": "IN_PROGRESS",
            "workflows/req-001/tasks/backend/assigned_agent": "dead-agent",
            "workflows/req-001/tasks/backend/started_at": "2025-04-22T10:00:00",
            "workflows/req-001/tasks/backend/retry_count": "0",
        }
        consul = _make_store(store)
        consul.list_services = Mock(return_value=[])

        wd = Watchdog(consul, poll_interval=1, task_timeout_seconds=3600, max_retry=3)
        wd._tick()

        put_calls = consul.kv_put.call_args_list
        reason_written = any(
            "last_recovery_reason" in str(c)
            for c in put_calls
        )
        assert reason_written, "recovery reason should be written"

    def test_multiple_workflows_scanned(self):
        """应扫描所有 workflow 中的 IN_PROGRESS 任务。"""
        store = {
            "workflows/req-001/published": "true",
            "workflows/req-001/tasks/backend/status": "IN_PROGRESS",
            "workflows/req-001/tasks/backend/assigned_agent": "dead-agent",
            "workflows/req-001/tasks/backend/started_at": "2025-04-22T10:00:00",
            "workflows/req-001/tasks/backend/retry_count": "0",
            "workflows/req-002/published": "true",
            "workflows/req-002/tasks/design/status": "IN_PROGRESS",
            "workflows/req-002/tasks/design/assigned_agent": "dead-agent",
            "workflows/req-002/tasks/design/started_at": "2025-04-22T10:00:00",
            "workflows/req-002/tasks/design/retry_count": "0",
        }
        consul = _make_store(store)
        consul.list_services = Mock(return_value=[])

        wd = Watchdog(consul, poll_interval=1, task_timeout_seconds=3600, max_retry=3)
        wd._tick()

        put_calls = consul.kv_put.call_args_list
        req001_pending = any(
            "req-001" in str(c) and c[0][1] == "PENDING"
            for c in put_calls
        )
        req002_pending = any(
            "req-002" in str(c) and c[0][1] == "PENDING"
            for c in put_calls
        )
        assert req001_pending, "req-001 backend should be recovered"
        assert req002_pending, "req-002 design should be recovered"
