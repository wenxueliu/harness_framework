"""start_instance 回归测试：全新实例首次启动不应报「运行注册冲突」。

背景：kv_get 对不存在的键返回 (None, 全局索引)，而 kv_put 的 CAS 把
缺失键视为索引 0。旧实现把 kv_get 返回的索引直接作为 CAS 基准，
导致全局索引 > 0 时首次启动必然 CAS 失败并抛 RUN_REGISTRATION_CONFLICT。
"""
from harness_framework.job_flows import INSTANCE_PREFIX, JobFlowService


def _seed_instance(service: JobFlowService, instance_id: str) -> None:
    service._write(f"{INSTANCE_PREFIX}/{instance_id}/meta", {
        "instance_id": instance_id,
        "template_id": "tpl-regression",
        "version_id": "",
        "name": "回归实例",
        "created_at": "2026-01-01T00:00:00Z",
    })
    service._write(f"{INSTANCE_PREFIX}/{instance_id}/status", {"state": "QUEUED", "revision": 0})


def test_start_instance_first_run_registers_run(mock_consul) -> None:
    """全新实例（无 current_run 键）首次启动应成功注册 run 并转入 RUNNING。"""
    service = JobFlowService(mock_consul)
    _seed_instance(service, "inst-first-run")

    result = service.start_instance("inst-first-run", actor="tester")

    assert result["instance"]["status"]["state"] == "RUNNING"
    run = result["run"]
    assert isinstance(run, dict) and run.get("run_id")
    status, _ = mock_consul.kv_get(
        f"{INSTANCE_PREFIX}/inst-first-run/runs/{run['run_id']}/status"
    )
    assert status == "RUNNING"
    current_run, _ = mock_consul.kv_get(f"{INSTANCE_PREFIX}/inst-first-run/current_run")
    assert current_run == run["run_id"]


def test_start_instance_existing_finished_run_reuses_run_id(mock_consul) -> None:
    """已有终态 run 的实例再次启动沿用原 run_id，不新注册。"""
    service = JobFlowService(mock_consul)
    _seed_instance(service, "inst-restart")
    mock_consul.kv_put(f"{INSTANCE_PREFIX}/inst-restart/current_run", "run-old")
    mock_consul.kv_put(f"{INSTANCE_PREFIX}/inst-restart/runs/run-old/status", "FAILED")

    result = service.start_instance("inst-restart", actor="tester")

    assert result["run"]["run_id"] == "run-old"


def test_start_instance_first_run_with_real_local_store() -> None:
    """用真实 LocalStore（非 mock）验证：首次启动在全局索引 > 0 时仍能注册。

    MockConsulStore 的 kv_put 忽略 cas 参数，无法复现 CAS 冲突；
    LocalStore 的 kv_get 对缺失键返回全局索引、kv_put 严格比对 cas，
    旧代码在该场景下必然抛 RUN_REGISTRATION_CONFLICT。
    """
    from harness_framework.local_store import LocalStore

    service = JobFlowService(LocalStore())
    # 触发全局索引增长，模拟真实运行环境（索引从 100 起且持续递增）
    service.store.kv_put("warmup", "x")
    _seed_instance(service, "inst-real-store")

    result = service.start_instance("inst-real-store", actor="tester")

    assert result["instance"]["status"]["state"] == "RUNNING"
    run = result["run"]
    assert isinstance(run, dict) and run.get("run_id")
    current_run, _ = service.store.kv_get(
        f"{INSTANCE_PREFIX}/inst-real-store/current_run"
    )
    assert current_run == run["run_id"]


def test_start_instance_running_run_is_idempotent(mock_consul) -> None:
    """run 仍在 RUNNING 时重复启动直接返回现状，不再注册。"""
    import json

    service = JobFlowService(mock_consul)
    _seed_instance(service, "inst-idempotent")
    mock_consul.kv_put(f"{INSTANCE_PREFIX}/inst-idempotent/current_run", "run-live")
    mock_consul.kv_put(f"{INSTANCE_PREFIX}/inst-idempotent/runs/run-live/status", "RUNNING")

    service.start_instance("inst-idempotent", actor="tester")

    # 未注册新 run，实例状态未被改动
    current_run, _ = mock_consul.kv_get(f"{INSTANCE_PREFIX}/inst-idempotent/current_run")
    assert current_run == "run-live"
    runs = mock_consul.kv_get(f"{INSTANCE_PREFIX}/inst-idempotent/runs/", recurse=True)[0] or []
    assert len(runs) == 1
    status, _ = mock_consul.kv_get(f"{INSTANCE_PREFIX}/inst-idempotent/status")
    assert json.loads(status)["state"] == "QUEUED"
