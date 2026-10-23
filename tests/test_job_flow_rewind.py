"""rewind：从指定节点重来，下游闭包（含已 DONE）全部重置。"""
from __future__ import annotations

import json

import pytest

from harness_framework.aggregator import Aggregator
from harness_framework.api_errors import APIError
from harness_framework.job_flows import INSTANCE_PREFIX, JobFlowService
from harness_framework.run_manager import RunManager


def _task(task_id: str, depends_on: list[str]) -> dict:
    return {
        "id": task_id,
        "type": "backend",
        "do": f"执行 {task_id}",
        "depends_on": depends_on,
        "input": "上游产物" if depends_on else "",
        "output": f"{task_id} 产物",
    }


def _service(store, instance_id: str = "inst-rewind") -> JobFlowService:
    service = JobFlowService(store)
    service.create_template({
        "template_id": "tpl-rewind", "name": "rewind flow",
        "tasks": [
            _task("build", []),
            _task("test", ["build"]),
            _task("deploy", ["test"]),
        ],
    }, "alice")
    version = service.publish_template(
        "tpl-rewind", {"expected_revision": 1}, "alice", "publish-rewind",
    )["version"]
    service.create_instance({
        "template_id": "tpl-rewind", "version_id": version["version_id"],
        "instance_id": instance_id,
    }, "alice", "create-rewind")
    service.start_instance(instance_id, "alice")
    return service


def _base(instance_id: str, task_id: str) -> str:
    return f"{INSTANCE_PREFIX}/{instance_id}/tasks/{task_id}"


def _set_state(store, instance_id: str, task_id: str, state: str) -> None:
    store.kv_put(f"{_base(instance_id, task_id)}/status", json.dumps({
        "task_id": task_id, "state": state, "attempt_count": 1,
    }))


def _state(store, instance_id: str, task_id: str) -> str:
    raw, _ = store.kv_get(f"{_base(instance_id, task_id)}/status")
    return json.loads(raw)["state"]


def test_rewind_resets_target_and_done_downstream(mock_consul):
    service = _service(mock_consul)
    for task_id in ("build", "test", "deploy"):
        _set_state(mock_consul, "inst-rewind", task_id, "DONE")
    mock_consul.kv_put(f"{INSTANCE_PREFIX}/inst-rewind/status", json.dumps(
        {"state": "SUCCEEDED", "revision": 3}
    ))
    mock_consul.kv_put(f"{_base('inst-rewind', 'test')}/output", json.dumps(
        {"declared": "test 产物"}
    ))
    mock_consul.kv_put(f"{_base('inst-rewind', 'test')}/attempts/attempt-1-abc/state", "DONE")

    result = service.rewind_task(
        "inst-rewind", "test", {"reason": "契约变化"}, "alice",
    )

    assert result["rewound_tasks"] == ["deploy", "test"]
    assert _state(mock_consul, "inst-rewind", "build") == "DONE"
    assert _state(mock_consul, "inst-rewind", "test") == "PENDING"
    assert _state(mock_consul, "inst-rewind", "deploy") == "BLOCKED"
    instance = result["instance"]
    assert instance["status"]["state"] == "RUNNING"

    # 活动产物被清除，旧值归档到 rewinds/<rewind_id>/
    assert mock_consul.kv_get(f"{_base('inst-rewind', 'test')}/output")[0] is None
    rewind_id = result["rewind_id"]
    archive = f"{INSTANCE_PREFIX}/inst-rewind/rewinds/{rewind_id}/tasks/test"
    assert mock_consul.kv_get(f"{archive}/output")[0] is not None
    assert mock_consul.kv_get(f"{archive}/attempts/attempt-1-abc/state")[0] == "DONE"


def test_rewind_without_downstream_only_touches_target(mock_consul):
    service = _service(mock_consul)
    for task_id in ("build", "test", "deploy"):
        _set_state(mock_consul, "inst-rewind", task_id, "DONE")

    result = service.rewind_task(
        "inst-rewind", "test", {"reset_downstream": False}, "alice",
    )

    assert result["rewound_tasks"] == ["test"]
    assert _state(mock_consul, "inst-rewind", "test") == "PENDING"
    assert _state(mock_consul, "inst-rewind", "deploy") == "DONE"


def test_rewind_blocks_target_when_upstream_not_done(mock_consul):
    service = _service(mock_consul)
    _set_state(mock_consul, "inst-rewind", "build", "PENDING")
    _set_state(mock_consul, "inst-rewind", "test", "DONE")
    _set_state(mock_consul, "inst-rewind", "deploy", "DONE")

    service.rewind_task("inst-rewind", "test", {}, "alice")

    assert _state(mock_consul, "inst-rewind", "build") == "PENDING"
    assert _state(mock_consul, "inst-rewind", "test") == "BLOCKED"
    assert _state(mock_consul, "inst-rewind", "deploy") == "BLOCKED"


def test_rewind_rejects_unknown_task_and_superseded_instance(mock_consul):
    service = _service(mock_consul)

    with pytest.raises(APIError) as missing:
        service.rewind_task("inst-rewind", "nope", {}, "alice")
    assert missing.value.code == "TASK_NOT_FOUND"

    mock_consul.kv_put(f"{INSTANCE_PREFIX}/inst-rewind/status", json.dumps(
        {"state": "SUPERSEDED", "revision": 4}
    ))
    with pytest.raises(APIError) as superseded:
        service.rewind_task("inst-rewind", "build", {}, "alice")
    assert superseded.value.code == "INSTANCE_STATE_INVALID"


def test_rewritten_target_completion_reactivates_downstream(mock_consul):
    service = _service(mock_consul)
    for task_id in ("build", "test", "deploy"):
        _set_state(mock_consul, "inst-rewind", task_id, "DONE")
    service.rewind_task("inst-rewind", "test", {}, "alice")

    _set_state(mock_consul, "inst-rewind", "test", "DONE")
    aggregator = Aggregator(
        mock_consul, run_manager=RunManager(mock_consul, instance_mode=True),
    )
    aggregator._process_instance("inst-rewind")

    assert _state(mock_consul, "inst-rewind", "deploy") == "PENDING"
