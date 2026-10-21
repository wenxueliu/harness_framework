"""提示词只包含 do / input / output 与实例参数。"""
from __future__ import annotations

import json

from harness_framework.acp_dispatcher import ACPDispatcher, JobFlowACPDispatcher
from harness_framework.run_manager import RunManager
from tests.conftest import MockConsulStore
from tests.test_acp_dispatcher import _store


def _workflow_dispatcher() -> ACPDispatcher:
    store = _store("backend")
    return ACPDispatcher(
        store, RunManager(store),
        commands={"codex": ["codex-acp"]}, client_factory=lambda *a, **k: None,
    )


def _jobflow_dispatcher() -> JobFlowACPDispatcher:
    return JobFlowACPDispatcher(MockConsulStore({}), commands={"codex": ["codex-acp"]})


def _package(prompt: str) -> dict:
    return json.loads(prompt.split("TASK PACKAGE:\n", 1)[1])


def test_package_contains_only_do_input_output_and_parameters():
    prompt = _jobflow_dispatcher()._build_prompt("inst-1", "build", {
        "type": "backend", "do": "做事", "input": "上游产物", "output": "结果",
        "description": "冗余说明", "service_name": "svc",
        "agent_contract": {"responsibilities": ["x"]},
        "completion_contract": {"required_artifacts": ["a"]},
        "output_format": {"kind": "marker", "pattern": "V"},
    })

    package = _package(prompt)
    assert set(package) == {"do", "input", "output", "parameters"}
    assert package["do"] == "做事"
    assert package["input"] == "上游产物"
    assert package["output"] == "结果"
    assert package["parameters"] == {}


def test_input_carries_injected_context_and_upstream_outputs():
    store = MockConsulStore({
        "jobflows/instances/inst-1/knowledge/facts/region": '{"value":"cn"}',
        "jobflows/instances/inst-1/tasks/upstream/output": json.dumps({
            "declared": "上游产物.md", "evidence": {"path": "up.md"},
        }),
    })
    dispatcher = JobFlowACPDispatcher(store, commands={"codex": ["codex-acp"]})

    prompt = dispatcher._build_prompt("inst-1", "build", {
        "type": "backend", "do": "做事", "input": "设计文档",
        "context_inputs": ["facts/region"], "depends_on": ["upstream"],
    })

    composed = _package(prompt)["input"]
    assert "设计文档" in composed
    assert "[上下文] facts/region" in composed
    assert "[上游产物] upstream: 上游产物.md" in composed


def test_prompt_never_mentions_stage_bridge():
    for prompt in (
        _workflow_dispatcher()._build_prompt("req-1", "build", {"type": "backend"}),
        _jobflow_dispatcher()._build_prompt("inst-1", "build", {"type": "backend"}),
    ):
        assert "stage-bridge" not in prompt
        assert "Do not claim success when verification fails." in prompt
