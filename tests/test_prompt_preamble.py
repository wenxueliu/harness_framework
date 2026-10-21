"""提示词开场白按 scope 收敛：stage-bridge 只属于旧 workflow 范围。"""
from __future__ import annotations

import json

from harness_framework.acp_dispatcher import ACPDispatcher, JobFlowACPDispatcher
from harness_framework.run_manager import RunManager
from tests.conftest import MockConsulStore
from tests.test_acp_dispatcher import _store


def _workflow_dispatcher(completion: dict | None = None) -> ACPDispatcher:
    store = _store("backend")
    if completion is not None:
        store.kv_put("workflows/req-1/tasks/build/completion_contract",
                     json.dumps(completion))
    return ACPDispatcher(
        store, RunManager(store),
        commands={"codex": ["codex-acp"]}, client_factory=lambda *a, **k: None,
    )


def _jobflow_dispatcher() -> JobFlowACPDispatcher:
    store = MockConsulStore({})
    return JobFlowACPDispatcher(
        store, commands={"codex": ["codex-acp"]},
    )


def test_workflow_scope_mentions_stage_bridge_only_with_completion_contract():
    with_contract = _workflow_dispatcher(
        {"required_artifacts": ["impl"], "required_gates": ["tests"]},
    )._build_prompt("req-1", "build", {
        "type": "backend",
        "completion_contract": json.dumps(
            {"required_artifacts": ["impl"], "required_gates": ["tests"]}),
    })
    assert "stage-bridge" in with_contract

    without_contract = _workflow_dispatcher()._build_prompt(
        "req-1", "build", {"type": "backend"})
    assert "stage-bridge" not in without_contract


def test_jobflow_scope_never_mentions_stage_bridge():
    dispatcher = _jobflow_dispatcher()
    prompt = dispatcher._build_prompt("inst-1", "build", {
        "type": "backend",
        "completion_contract": json.dumps(
            {"required_artifacts": ["impl"], "required_gates": ["tests"]}),
    })

    assert "stage-bridge" not in prompt
    assert "Do not claim success when verification fails." in prompt


def test_jobflow_scope_does_not_block_on_legacy_completion_contract():
    dispatcher = _jobflow_dispatcher()
    assert dispatcher._missing_completion_requirements("inst-1", "build") == []
