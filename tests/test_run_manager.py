"""
RunManager 单元测试
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from harness_framework.run_manager import RunManager
from tests.conftest import MockConsulStore


def make_run_manager(initial: dict | None = None) -> tuple[RunManager, MockConsulStore, MagicMock]:
    store = MockConsulStore(initial)
    consul = MagicMock()
    consul.kv_get = MagicMock(side_effect=store.kv_get)
    consul.kv_put = MagicMock(side_effect=store.kv_put)
    consul.kv_delete = MagicMock(side_effect=store.kv_delete)
    return RunManager(consul), store, consul


class TestRunLifecycle:
    """Run 生命周期测试。"""

    def test_create_run_when_none_exists(self):
        rm, store, consul = make_run_manager()
        run_id = rm.get_or_create_run("req-001", "aggregator")
        assert run_id.startswith("run-")
        assert len(run_id) == 16  # "run-" + 12 hex
        run_status, _ = store.kv_get(f"workflows/req-001/runs/{run_id}/status")
        assert run_status == "RUNNING"

    def test_reuse_active_run(self):
        rm, store, consul = make_run_manager()
        run_id_1 = rm.get_or_create_run("req-001", "aggregator")
        run_id_2 = rm.get_or_create_run("req-001", "watchdog")
        assert run_id_1 == run_id_2

    def test_create_new_run_after_completion(self):
        rm, store, consul = make_run_manager()
        run_id_1 = rm.get_or_create_run("req-001", "aggregator")
        rm.end_run("req-001", run_id_1, "COMPLETED")
        run_id_2 = rm.get_or_create_run("req-001", "aggregator")
        assert run_id_1 != run_id_2

    def test_create_new_run_after_aborted(self):
        rm, store, consul = make_run_manager()
        run_id_1 = rm.get_or_create_run("req-001", "webapi")
        rm.end_run("req-001", run_id_1, "ABORTED")
        run_id_2 = rm.get_or_create_run("req-001", "aggregator")
        assert run_id_1 != run_id_2

    def test_end_run_clears_current(self):
        rm, store, consul = make_run_manager()
        run_id = rm.get_or_create_run("req-001", "aggregator")
        rm.end_run("req-001", run_id, "COMPLETED")
        current, _ = store.kv_get("workflows/req-001/current_run")
        assert current is None

    def test_end_run_sets_finished_at(self):
        rm, store, consul = make_run_manager()
        run_id = rm.get_or_create_run("req-001", "aggregator")
        rm.end_run("req-001", run_id, "FAILED")
        finished, _ = store.kv_get(f"workflows/req-001/runs/{run_id}/finished_at")
        assert finished is not None
        status, _ = store.kv_get(f"workflows/req-001/runs/{run_id}/status")
        assert status == "FAILED"


class TestTransitions:
    """状态转换审计日志测试。"""

    def test_record_and_retrieve_transitions(self):
        rm, store, consul = make_run_manager()
        run_id = rm.get_or_create_run("req-001", "aggregator")
        rm.record_transition("req-001", run_id, "design", "", "BLOCKED",
                             "aggregator", "dependencies not satisfied")
        rm.record_transition("req-001", run_id, "design", "BLOCKED", "PENDING",
                             "aggregator", "dependencies satisfied")
        rm.record_transition("req-001", run_id, "design", "PENDING", "IN_PROGRESS",
                             "agent-001", "claimed by agent")

        transitions = rm.get_transitions("req-001", run_id)
        assert len(transitions) == 3
        assert transitions[0]["previous_state"] == ""
        assert transitions[0]["new_state"] == "BLOCKED"
        assert transitions[1]["previous_state"] == "BLOCKED"
        assert transitions[1]["new_state"] == "PENDING"
        assert transitions[2]["new_state"] == "IN_PROGRESS"

    def test_transitions_ordered_by_timestamp(self):
        rm, store, consul = make_run_manager()
        run_id = rm.get_or_create_run("req-001", "aggregator")
        rm.record_transition("req-001", run_id, "test", "PENDING", "IN_PROGRESS",
                             "agent-002", "claimed")
        # 再做一次
        rm.record_transition("req-001", run_id, "test", "IN_PROGRESS", "DONE",
                             "agent-002", "completed")

        transitions = rm.get_transitions("req-001", run_id)
        assert len(transitions) == 2
        assert transitions[0]["new_state"] == "IN_PROGRESS"
        assert transitions[1]["new_state"] == "DONE"

    def test_empty_transitions(self):
        rm, store, consul = make_run_manager()
        run_id = rm.get_or_create_run("req-001", "aggregator")
        transitions = rm.get_transitions("req-001", run_id)
        assert transitions == []


class TestRunCompletion:
    """Run 自动完成检测测试。"""

    def _seed_tasks(self, store: MockConsulStore, req_id: str,
                    tasks: dict[str, str]) -> None:
        for name, status in tasks.items():
            store.kv_put(f"workflows/{req_id}/tasks/{name}/status", status)

    def test_check_completion_all_done(self):
        rm, store, consul = make_run_manager()
        run_id = rm.get_or_create_run("req-001", "aggregator")
        self._seed_tasks(store, "req-001", {
            "design": "DONE", "backend": "DONE", "test": "DONE",
        })
        rm.check_run_completion("req-001", run_id)
        run_status, _ = store.kv_get(f"workflows/req-001/runs/{run_id}/status")
        assert run_status == "COMPLETED"

    def test_check_completion_one_failed(self):
        rm, store, consul = make_run_manager()
        run_id = rm.get_or_create_run("req-001", "aggregator")
        self._seed_tasks(store, "req-001", {
            "design": "DONE", "backend": "FAILED", "test": "DONE",
        })
        rm.check_run_completion("req-001", run_id)
        run_status, _ = store.kv_get(f"workflows/req-001/runs/{run_id}/status")
        assert run_status == "FAILED"

    def test_check_completion_all_aborted(self):
        rm, store, consul = make_run_manager()
        run_id = rm.get_or_create_run("req-001", "aggregator")
        self._seed_tasks(store, "req-001", {
            "design": "ABORTED", "backend": "ABORTED",
        })
        rm.check_run_completion("req-001", run_id)
        run_status, _ = store.kv_get(f"workflows/req-001/runs/{run_id}/status")
        assert run_status == "ABORTED"

    def test_check_completion_still_running(self):
        rm, store, consul = make_run_manager()
        run_id = rm.get_or_create_run("req-001", "aggregator")
        self._seed_tasks(store, "req-001", {
            "design": "DONE", "backend": "IN_PROGRESS", "test": "BLOCKED",
        })
        rm.check_run_completion("req-001", run_id)
        run_status, _ = store.kv_get(f"workflows/req-001/runs/{run_id}/status")
        assert run_status == "RUNNING"

    def test_check_completion_failed_with_skipped_downstream(self):
        rm, store, consul = make_run_manager()
        run_id = rm.get_or_create_run("req-001", "aggregator")
        self._seed_tasks(store, "req-001", {
            "design": "DONE",
            "backend": "FAILED",
            "test": "SKIPPED_UPSTREAM_FAILED",
        })
        rm.check_run_completion("req-001", run_id)
        run_status, _ = store.kv_get(
            f"workflows/req-001/runs/{run_id}/status"
        )
        assert run_status == "FAILED"

    def test_check_completion_no_tasks(self):
        rm, store, consul = make_run_manager()
        run_id = rm.get_or_create_run("req-001", "aggregator")
        rm.check_run_completion("req-001", run_id)
        run_status, _ = store.kv_get(f"workflows/req-001/runs/{run_id}/status")
        assert run_status == "RUNNING"


class TestQueryMethods:
    """查询方法测试。"""

    def test_list_runs_returns_empty(self):
        rm, store, consul = make_run_manager()
        runs = rm.list_runs("req-001")
        assert runs == []

    def test_list_runs_returns_multiple(self):
        rm, store, consul = make_run_manager()
        run_id_1 = rm.get_or_create_run("req-001", "aggregator")
        rm.end_run("req-001", run_id_1, "COMPLETED")
        run_id_2 = rm.get_or_create_run("req-001", "webapi")
        rm.end_run("req-001", run_id_2, "FAILED")

        runs = rm.list_runs("req-001")
        assert len(runs) == 2
        # 按 started_at 降序
        assert runs[0]["run_id"] == run_id_2
        assert runs[1]["run_id"] == run_id_1

    def test_get_run_returns_details(self):
        rm, store, consul = make_run_manager()
        run_id = rm.get_or_create_run("req-001", "aggregator")
        run = rm.get_run("req-001", run_id)
        assert run is not None
        assert run["run_id"] == run_id
        assert run["status"] == "RUNNING"
        assert "started_at" in run
        assert "started_by" in run
        assert "summary" in run

    def test_get_run_nonexistent(self):
        rm, store, consul = make_run_manager()
        run = rm.get_run("req-001", "run-nonexistent")
        assert run is None

    def test_get_run_nonexistent_req(self):
        rm, store, consul = make_run_manager()
        run = rm.get_run("nonexistent", "run-nonexistent")
        assert run is None


class TestSessionIndex:
    """Session 索引测试。"""

    def test_record_session_start_and_end(self):
        rm, store, consul = make_run_manager()
        run_id = rm.get_or_create_run("req-001", "aggregator")
        rm.record_session_start("req-001", run_id, "backend",
                                "agent-001-1234567", "agent-001")
        rm.record_session_end("req-001", run_id, "backend",
                              event_count=5, error_count=0,
                              status="completed", summary="后端开发完成")

        sessions = rm.get_run_sessions("req-001", run_id)
        assert len(sessions) == 1
        s = sessions[0]
        assert s["task_name"] == "backend"
        assert s["session_id"] == "agent-001-1234567"
        assert s["agent_id"] == "agent-001"
        assert s["status"] == "completed"
        assert s["event_count"] == "5"
        assert s["error_count"] == "0"
        assert s["summary"] == "后端开发完成"

    def test_get_run_sessions_empty(self):
        rm, store, consul = make_run_manager()
        run_id = rm.get_or_create_run("req-001", "aggregator")
        sessions = rm.get_run_sessions("req-001", run_id)
        assert sessions == []

    def test_multiple_sessions_per_run(self):
        rm, store, consul = make_run_manager()
        run_id = rm.get_or_create_run("req-001", "aggregator")
        rm.record_session_start("req-001", run_id, "design",
                                "agent-003-100", "agent-003")
        rm.record_session_end("req-001", run_id, "design",
                              event_count=3, error_count=0, status="completed")
        rm.record_session_start("req-001", run_id, "backend",
                                "agent-001-200", "agent-001")
        rm.record_session_end("req-001", run_id, "backend",
                              event_count=7, error_count=2, status="error",
                              summary="后端出错")

        sessions = rm.get_run_sessions("req-001", run_id)
        assert len(sessions) == 2
        assert sessions[0]["task_name"] == "backend"
        assert sessions[1]["task_name"] == "design"

    def test_export_run_sessions(self):
        rm, store, consul = make_run_manager()
        run_id = rm.get_or_create_run("req-001", "aggregator")
        rm.record_session_start("req-001", run_id, "design",
                                "agent-003-100", "agent-003")
        rm.record_session_end("req-001", run_id, "design",
                              event_count=3, error_count=0, status="completed")

        # 写入 session events
        event_payload = json.dumps({
            "ts": "2026-05-05T12:00:00Z",
            "agent_id": "agent-003",
            "level": "info",
            "message": "开始设计",
            "step_type": "EXEC_START",
            "run_id": run_id,
        })
        store.kv_put(
            f"workflows/req-001/sessions/design/agent-003-100/events/0001",
            event_payload,
        )

        exported = rm.export_run_sessions("req-001", run_id)
        assert exported["req_id"] == "req-001"
        assert exported["run_id"] == run_id
        assert len(exported["sessions"]) == 1
        assert "design" in exported["session_events"]
        assert len(exported["session_events"]["design"]) == 1
