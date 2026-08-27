from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).parents[1] / "skills" / "stage-bridge" / "scripts"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MATCHING = _load("stage_bridge_matching", "_matching.py")
CLAIM_NEXT = _load("stage_bridge_claim_next", "claim_next_task.py")
CLAIM_DIRECT = _load("stage_bridge_claim_direct", "claim_task.py")
REGISTER = _load("stage_bridge_register", "register_agent.py")
WORKER = _load("stage_bridge_worker_matching", "worker.py")


def test_task_target_uses_agent_name_not_service_name():
    meta = {"agent_name": "backend-agent", "service_name": "users"}
    assert MATCHING.task_target_name(meta) == "backend-agent"
    assert MATCHING.task_matches_agent(meta, "backend-agent")
    assert not MATCHING.task_matches_agent(meta, "users")


def test_service_name_is_never_a_task_target():
    meta = {"service_name": "users"}
    assert MATCHING.task_target_name(meta) == ""
    assert not MATCHING.task_matches_agent(meta, "users")


def test_registration_does_not_use_service_name_as_agent_name(monkeypatch):
    monkeypatch.setattr(REGISTER, "env", lambda name, default="", **_: {
        "AGENT_ID": "backend-01", "SERVICE_NAME": "users",
    }.get(name, default))
    monkeypatch.setattr(
        "sys.argv",
        ["register_agent.py", "--capabilities", "backend", "--service", "users"],
    )
    with pytest.raises(SystemExit) as exc:
        REGISTER.main()
    assert exc.value.code == 2


def test_task_without_target_is_not_claimable():
    assert not MATCHING.task_matches_agent({}, "backend-agent")


def test_claim_next_filters_out_other_agent_names(monkeypatch):
    monkeypatch.setattr(CLAIM_NEXT, "kv_get", lambda *_args, **_kwargs: (None, 0))
    tasks = [
        {"req_id": "r1", "task_name": "mine", "agent_name": "backend-agent",
         "service_name": "users", "type": "backend", "req_priority": 0},
        {"req_id": "r1", "task_name": "other", "agent_name": "test-agent",
         "service_name": "users", "type": "test", "req_priority": 100},
    ]
    ranked = CLAIM_NEXT.filter_and_rank_tasks(
        tasks, "instance-1", ["backend", "test"], "backend-agent",
    )
    assert [task["task_name"] for task in ranked] == ["mine"]


def test_worker_filters_out_other_agent_names(monkeypatch):
    metadata = {
        "mine": {"agent_name": "backend-agent", "service_name": "users",
                 "type": "backend"},
        "other": {"agent_name": "test-agent", "service_name": "users",
                  "type": "test"},
    }
    monkeypatch.setattr(
        WORKER, "load_task_meta",
        lambda _req_id, task_name: metadata[task_name],
    )
    monkeypatch.setattr(WORKER, "kv_get", lambda *_args, **_kwargs: (None, 0))
    ranked = WORKER.rank_tasks(
        [{"req_id": "r1", "task_name": "mine"},
         {"req_id": "r1", "task_name": "other"}],
        "instance-1", "backend-agent", ["dev", "test"],
    )
    assert [task["task_name"] for task, _meta in ranked] == ["mine"]


def test_worker_rechecks_agent_name_before_cas(monkeypatch):
    writes = []
    monkeypatch.setattr(
        WORKER, "kv_get",
        lambda key, **_kwargs: (
            ("PENDING", 4) if key.endswith("/status") else (None, 0)
        ),
    )
    monkeypatch.setattr(
        WORKER, "load_task_meta",
        lambda _req_id, _task_name: {"agent_name": "backend-agent"},
    )
    monkeypatch.setattr(
        WORKER, "kv_put",
        lambda key, value, **kwargs: writes.append((key, value, kwargs)) or True,
    )
    success, result = WORKER.claim_task(
        "r1", "backend", "instance-1", "test-agent",
    )
    assert not success
    assert "backend-agent" in result["error"]
    assert writes == []


def test_claim_rejects_mismatched_agent_before_cas(monkeypatch):
    base = "workflows/r1/tasks/backend"
    values = {
        f"{base}/status": "PENDING",
        f"{base}/agent_name": "backend-agent",
        f"{base}/service_name": "users",
    }
    writes = []
    monkeypatch.setattr(
        CLAIM_NEXT, "kv_get", lambda key, **_kwargs: (values.get(key), 1),
    )
    monkeypatch.setattr(
        CLAIM_NEXT, "kv_put",
        lambda key, value, **kwargs: writes.append((key, value, kwargs)) or True,
    )
    success, result = CLAIM_NEXT.claim_task(
        "r1", "backend", "instance-1", "test-agent",
    )
    assert not success
    assert "backend-agent" in result["error"]
    assert writes == []


def test_direct_claim_cannot_bypass_agent_name(monkeypatch):
    base = "workflows/r1/tasks/backend"
    values = {
        "workflows/r1/status": "IN_PROGRESS",
        "workflows/r1/control": None,
        f"{base}/status": "PENDING",
        f"{base}/agent_name": "backend-agent",
        f"{base}/service_name": "users",
        "agents/instance-1/name": "test-agent",
    }
    writes = []
    monkeypatch.setattr(CLAIM_DIRECT, "env", lambda name, default="", **_: {
        "AGENT_ID": "instance-1", "AGENT_NAME": "test-agent",
    }.get(name, default))
    monkeypatch.setattr(
        CLAIM_DIRECT, "kv_get", lambda key, **_kwargs: (values.get(key), 1),
    )
    monkeypatch.setattr(
        CLAIM_DIRECT, "kv_put",
        lambda key, value, **kwargs: writes.append((key, value, kwargs)) or True,
    )

    class Rejected(Exception):
        pass

    monkeypatch.setattr(
        CLAIM_DIRECT, "die",
        lambda message, code=1: (_ for _ in ()).throw(Rejected(message)),
    )
    monkeypatch.setattr("sys.argv", ["claim_task.py", "r1", "backend"])
    try:
        CLAIM_DIRECT.main()
    except Rejected as exc:
        assert "backend-agent" in str(exc)
    else:
        raise AssertionError("mismatched Agent Name claimed a task")
    assert writes == []


def test_direct_claim_requires_registered_name(monkeypatch):
    monkeypatch.setattr(CLAIM_DIRECT, "env", lambda name, default="", **_: {
        "AGENT_ID": "instance-1", "AGENT_NAME": "backend-agent",
    }.get(name, default))
    monkeypatch.setattr(CLAIM_DIRECT, "kv_get", lambda *_args, **_kwargs: (None, 0))

    class Rejected(Exception):
        pass

    monkeypatch.setattr(
        CLAIM_DIRECT, "die",
        lambda message, code=1: (_ for _ in ()).throw(Rejected((message, code))),
    )
    monkeypatch.setattr("sys.argv", ["claim_task.py", "r1", "backend"])
    with pytest.raises(Rejected) as exc:
        CLAIM_DIRECT.main()
    assert "未注册名称" in exc.value.args[0][0]
    assert exc.value.args[0][1] == 2


def test_registration_publishes_logical_agent_name(monkeypatch):
    payloads = []
    writes = {}
    monkeypatch.setattr(REGISTER, "env", lambda name, default="", **_: {
        "AGENT_ID": "backend-01", "AGENT_NAME": "backend-agent",
    }.get(name, default))
    monkeypatch.setattr(REGISTER, "service_register", payloads.append)
    monkeypatch.setattr(
        REGISTER, "kv_put",
        lambda key, value, **_kwargs: writes.__setitem__(key, value) or True,
    )
    monkeypatch.setattr(REGISTER, "emit_json", lambda _value: None)
    monkeypatch.setattr(
        "sys.argv", ["register_agent.py", "--capabilities", "backend"],
    )
    REGISTER.main()
    assert payloads[0]["Meta"]["agent_name"] == "backend-agent"
    assert writes["agents/backend-01/name"] == "backend-agent"
