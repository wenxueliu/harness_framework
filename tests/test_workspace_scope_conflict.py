"""write_scope 冲突检测必须认得出作业流实例的已结束任务。

回归背景：冲突检测读兄弟任务的 status 时写死了 `workflows/<req>/tasks/<task>/status`，
而作业流实例的任务状态在 `jobflows/instances/<req>/tasks/<task>/status`。
后果是兄弟任务明明已经 DONE，仍然被当成「运行中」，同 scope 的下游任务被
永久 defer（t2 实例的 test 任务一直拿不到调度）。
"""
from __future__ import annotations

import json

from harness_framework.project_groups import ProjectGroupService
from harness_framework.workspace_manager import WorkspaceManager
from harness_framework.workspace_security import WorkspaceSecurity
from tests.conftest import MockConsulStore


def _manager(store: MockConsulStore, tmp_path) -> WorkspaceManager:
    return WorkspaceManager(
        store, WorkspaceSecurity({"projects": str(tmp_path)}),
        ProjectGroupService(store), provision_root_alias="projects",
    )


def _seed_binding(store: MockConsulStore, req_id: str, run_id: str,
                  task_id: str, workspace_id: str = "rws-1") -> None:
    store._store[f"workflows/{req_id}/runs/{run_id}/tasks/{task_id}/attempts/a1/workspace-binding"] = json.dumps({
        "task_id": task_id, "workspace_id": workspace_id,
        "binding_type": "RUN_SHARED", "write_scope": [],
    })


def _seed_run_workspace(store: MockConsulStore, req_id: str, run_id: str,
                        workspace_id: str = "rws-1") -> None:
    store._store[f"workflows/{req_id}/runs/{run_id}/workspace/record"] = json.dumps({
        "run_workspace_id": workspace_id, "req_id": req_id, "run_id": run_id,
        "strategy": "ORIGINAL", "status": "READY", "read_write": True,
        "root_ref": "projects:.",
    })


def test_finished_job_flow_task_does_not_block_sibling(tmp_path):
    store = MockConsulStore()
    manager = _manager(store, tmp_path)
    req_id, run_id = "inst-1", "run-1"
    _seed_run_workspace(store, req_id, run_id)
    _seed_binding(store, req_id, run_id, "dev")
    # 作业流实例布局：dev 已经 DONE
    store._store[f"jobflows/instances/{req_id}/tasks/dev/status"] = json.dumps({
        "task_id": "dev", "state": "DONE", "attempt_count": 1,
    })

    assert manager.has_write_scope_conflict(req_id, run_id, "test", ()) is False


def test_running_job_flow_task_still_blocks_sibling(tmp_path):
    store = MockConsulStore()
    manager = _manager(store, tmp_path)
    req_id, run_id = "inst-2", "run-2"
    _seed_run_workspace(store, req_id, run_id)
    _seed_binding(store, req_id, run_id, "dev")
    store._store[f"jobflows/instances/{req_id}/tasks/dev/status"] = json.dumps({
        "task_id": "dev", "state": "IN_PROGRESS", "attempt_count": 1,
    })

    assert manager.has_write_scope_conflict(req_id, run_id, "test", ()) is True


def test_finished_legacy_workflow_task_does_not_block_sibling(tmp_path):
    store = MockConsulStore()
    manager = _manager(store, tmp_path)
    req_id, run_id = "req-1", "run-1"
    _seed_run_workspace(store, req_id, run_id)
    _seed_binding(store, req_id, run_id, "build")
    store._store[f"workflows/{req_id}/tasks/build/status"] = "DONE"

    assert manager.has_write_scope_conflict(req_id, run_id, "test", ()) is False
