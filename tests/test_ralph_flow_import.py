from __future__ import annotations

import json

import pytest

from harness_framework.job_flows import JobFlowService
from harness_framework.ralph_flow_import import (
    RalphFlowImportError, compile_workflow, parse_workflow,
)
from tests.conftest import MockConsulStore


SPEC = """
description: 先分析再实现
manual_step: [design]
steps:
  - id: design
    desc: 技术设计
    do: 写 design.md。
    input: 用户需求
    output: design.md
    check: 核对 design.md 是否完整。
    on_pass: implement
    on_fail: design
    max_fail_count: 3
  - id: implement
    desc: 实现
    do: 按设计实现并跑测试。
    input: design.md
    output: 测试通过
    check_voting:
      - check: 所有验收标准都已实现
      - check: 测试全绿
    on_pass: done
    on_fail: design
    max_fail_count: 5
"""

LOOP = """
description: 单步循环
steps:
  - id: loop
    desc: 迭代
    do: 完成任务。
    input: 任务
    output: 代码
    check: 检查完成度。
    on_pass: done
    on_fail: loop
    max_fail_count: 100
"""


def test_each_step_lowers_to_do_and_check_nodes():
    workflow = parse_workflow(SPEC)
    compiled = compile_workflow(workflow, name="demo", agent="opencode")
    tasks = {task["id"]: task for task in compiled["tasks"]}

    assert set(tasks) == {
        "design", "design__check", "implement", "implement__check1",
        "implement__check2",
    }
    # on_pass 变成依赖边
    assert tasks["design"]["depends_on"] == []
    assert tasks["design__check"]["depends_on"] == ["design"]
    assert tasks["implement"]["depends_on"] == ["design__check"]
    # 投票验证 → 并行 check 节点，下一个节点依赖全部（AND 合流 = 全票通过）
    assert tasks["implement__check1"]["depends_on"] == ["implement"]
    assert tasks["implement__check2"]["depends_on"] == ["implement"]
    # DO 节点带执行说明，CHECK 节点要求 VERDICT
    assert "DO:" in tasks["design"]["description"]
    assert "VERDICT" in tasks["design__check"]["description"]
    assert tasks["design__check"]["require_verdict"] is True
    # on_fail 变成 check 节点上的回退目标 + 失败预算
    assert tasks["design__check"]["on_fail"] == {
        "task": "design", "max_fail_count": 3,
    }
    assert tasks["implement__check1"]["on_fail"] == {
        "task": "design", "max_fail_count": 5,
    }
    # 人工步骤标记为需要批准
    assert tasks["design__check"]["require_approval"] is True
    assert tasks["implement__check1"]["require_approval"] is False
    # ralph_flow 只剩溯源信息，语义都在契约字段里
    assert tasks["design"]["ralph_flow"] == {
        "source": "ralph-flow", "workflow": "demo", "step": "design",
    }
    assert tasks["design__check"]["ralph_flow"]["role"] == "check"


def test_adversarial_check_maps_to_independent_verifier_with_deny_edit():
    workflow = parse_workflow("""
adversarial_check:
  agent: codex
  system_prompt: |
    你是一个严格的代码审查员。
  timeout_ms: 1800000
steps:
  - id: a
    desc: a
    do: a
    input: i
    output: o
    check: c
    on_pass: done
    on_fail: a
    max_fail_count: 1
""")
    compiled = compile_workflow(workflow, agent="opencode", check_agent="claude")
    check = {task["id"]: task for task in compiled["tasks"]}["a__check"]

    # 独立验证者：agent 优先取 adversarial_check.agent
    assert check["agent"] == "codex"
    # 可以读文件、跑命令，但不能改工作区
    assert check["acp"] == {"permission_policy": "deny_edit"}
    # system_prompt 并入验证指令
    assert "你是一个严格的代码审查员。" in check["do"]
    assert "VERDICT: PASS" in check["do"]


def test_unknown_adversarial_agent_falls_back_to_check_agent():
    workflow = parse_workflow("""
adversarial_check:
  agent: ralph-check
steps:
  - id: a
    desc: a
    do: a
    input: i
    output: o
    check: c
    on_pass: done
    on_fail: a
    max_fail_count: 1
""")
    compiled = compile_workflow(workflow, agent="opencode", check_agent="claude")
    check = {task["id"]: task for task in compiled["tasks"]}["a__check"]
    assert check["agent"] == "claude"
    assert check["acp"] == {"permission_policy": "deny_edit"}


def test_check_nodes_can_use_a_different_agent():
    compiled = compile_workflow(
        parse_workflow(LOOP), agent="opencode", check_agent="claude")
    tasks = {task["id"]: task for task in compiled["tasks"]}
    assert tasks["loop"]["agent"] == "opencode"
    assert tasks["loop__check"]["agent"] == "claude"


def test_compile_single_loop_step():
    compiled = compile_workflow(parse_workflow(LOOP))
    ids = [task["id"] for task in compiled["tasks"]]
    assert ids == ["loop", "loop__check"]
    assert compiled["tasks"][1]["on_fail"] == {
        "task": "loop", "max_fail_count": 100,
    }


def test_compiled_template_is_accepted_by_job_flow_service():
    store = MockConsulStore({})
    service = JobFlowService(store)
    compiled = compile_workflow(parse_workflow(SPEC), name="demo")
    compiled["group_id"] = "unassigned"

    template = service.create_template(compiled, "local:test")

    assert template["template_id"].startswith("tpl-")
    draft = service.get_template(template["template_id"])
    tasks = {task["id"]: task for task in draft["draft"]["tasks"]}
    assert tasks["design__check"]["require_verdict"] is True
    assert tasks["design__check"]["on_fail"]["task"] == "design"


def test_unknown_references_are_rejected():
    with pytest.raises(RalphFlowImportError):
        compile_workflow(parse_workflow("""
steps:
  - id: a
    desc: a
    do: a
    input: i
    output: o
    on_pass: missing
    on_fail: a
    max_fail_count: 1
"""))


def test_sub_workflow_is_rejected():
    with pytest.raises(RalphFlowImportError):
        compile_workflow(parse_workflow("""
steps:
  - id: a
    desc: a
    workflow: nested
    input: i
    output: o
    on_pass: done
    on_fail: a
    max_fail_count: 1
"""))


def test_json_source_parses_without_yaml():
    source = json.dumps({"steps": [
        {"id": "a", "desc": "a", "do": "a", "input": "i", "output": "o",
         "on_pass": "done", "on_fail": "a", "max_fail_count": 1},
    ]})
    assert parse_workflow(source)["steps"][0]["id"] == "a"
