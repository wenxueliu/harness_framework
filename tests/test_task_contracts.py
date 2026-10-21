"""节点契约校验：do / input / output / output_format 与依赖、on_fail 自洽。"""
from __future__ import annotations

import json

import pytest

from harness_framework.api_errors import APIError
from harness_framework.job_flows import JobFlowService
from tests.conftest import MockConsulStore


def _service() -> JobFlowService:
    return JobFlowService(MockConsulStore({}))


def _create(tasks: list[dict], name: str = "demo") -> dict:
    return _service().create_template(
        {"name": name, "group_id": "unassigned", "tasks": tasks}, "local:test",
    )


def test_successor_requires_output():
    with pytest.raises(APIError) as exc:
        _create([
            {"id": "a", "do": "做事"},
            {"id": "b", "do": "接力", "input": "结果", "depends_on": ["a"]},
        ])
    assert "output" in str(exc.value)


def test_predecessor_requires_input():
    with pytest.raises(APIError) as exc:
        _create([
            {"id": "a", "do": "做事", "output": "结果"},
            {"id": "b", "do": "接力", "output": "结果2", "depends_on": ["a"]},
        ])
    assert "input" in str(exc.value)


def test_on_fail_requires_output():
    with pytest.raises(APIError) as exc:
        _create([
            {"id": "a", "do": "做事", "output": "结果"},
            {"id": "b", "do": "接力", "input": "结果", "output": "结果2",
             "depends_on": ["a"]},
            {"id": "c", "do": "收尾", "input": "结果2", "output": "结果3",
             "depends_on": ["b"],
             "on_fail": {"task": "a", "max_fail_count": 2}},
        ])
    assert "on_fail" in str(exc.value)


def test_on_fail_requires_output_format():
    with pytest.raises(APIError) as exc:
        _create([
            {"id": "a", "do": "做事", "output": "结果"},
            {"id": "b", "do": "接力", "input": "结果", "output": "结果2",
             "depends_on": ["a"],
             "on_fail": {"task": "a", "max_fail_count": 2}},
        ])
    assert "output_format" in str(exc.value)


def test_on_fail_target_must_exist():
    with pytest.raises(APIError) as exc:
        _create([
            {"id": "a", "do": "做事", "output": "结果"},
            {"id": "b", "do": "接力", "input": "结果", "output": "结果2",
             "depends_on": ["a"],
             "on_fail": {"task": "missing", "max_fail_count": 2},
             "output_format": {"kind": "marker", "pattern": "VERDICT"}},
        ])
    assert "on_fail" in str(exc.value)


def test_task_needs_do_or_description():
    with pytest.raises(APIError) as exc:
        _create([{"id": "a", "output": "结果"}])
    assert "do" in str(exc.value)


def test_valid_contract_template_is_accepted():
    template = _create([
        {"id": "a", "do": "做事", "output": "结果",
         "output_format": {"kind": "file", "path": "result.md"}},
        {"id": "b", "do": "接力", "input": "结果", "output": "结果2",
         "depends_on": ["a"],
         "on_fail": {"task": "a", "max_fail_count": 2},
         "output_format": {"kind": "marker", "pattern": "VERDICT"}},
    ])
    assert template["template_id"].startswith("tpl-")


def test_legacy_template_without_contract_fields_still_accepted():
    template = _create([
        {"id": "a", "name": "构建", "type": "backend", "agent": "codex",
         "depends_on": []},
        {"id": "b", "name": "测试", "type": "backend", "agent": "codex",
         "depends_on": ["a"]},
    ])
    assert template["template_id"].startswith("tpl-")


def test_contract_fields_survive_publish():
    store = MockConsulStore({})
    service = JobFlowService(store)
    service.create_template({
        "name": "demo", "group_id": "unassigned",
        "tasks": [{"id": "a", "do": "做事", "output": "结果"}],
    }, "local:test")
    template_id = service.list_templates()[0]["template_id"]
    service.publish_template(template_id, {}, "local:test", "key-1")

    version = service.list_versions(template_id)[0]
    manifest = json.loads(store._store[
        f"jobflows/templates/{template_id}/versions/{version['version_id']}/manifest"])
    assert manifest["tasks"][0]["do"] == "做事"
    assert manifest["tasks"][0]["output"] == "结果"
