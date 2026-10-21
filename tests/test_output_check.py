"""产物格式校验（output_format）。"""
from __future__ import annotations

import json

from harness_framework.output_check import verify_output


def test_file_output_must_exist_and_be_non_empty(tmp_path):
    spec = {"kind": "file", "path": "result.md"}
    ok, reason, _ = verify_output(spec, "", cwd=str(tmp_path))
    assert ok is False
    assert "result.md" in reason

    empty = tmp_path / "result.md"
    empty.write_text("")
    ok, reason, evidence = verify_output(spec, "", cwd=str(tmp_path))
    assert ok is False
    assert "字节" in reason

    empty.write_text("内容")
    ok, reason, evidence = verify_output(spec, "", cwd=str(tmp_path))
    assert ok is True
    assert evidence["path"] == "result.md"
    assert evidence["bytes"] > 0


def test_file_output_accepts_artifact_directory(tmp_path):
    artifact = tmp_path / ".ralph-flow-artifacts" / "build" / "result.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("内容")
    ok, _reason, evidence = verify_output(
        {"kind": "file", "path": "result.md"}, "", cwd=str(tmp_path),
        task_id="build",
    )
    assert ok is True
    assert evidence["path"].endswith("result.md")


def test_json_output_is_validated():
    spec = {
        "kind": "json",
        "required": ["summary", "files"],
        "types": {"summary": "string", "files": "array"},
    }
    ok, reason, _ = verify_output(spec, "随便说点什么")
    assert ok is False
    assert "JSON" in reason

    ok, reason, _ = verify_output(spec, '{"summary": "x"}')
    assert ok is False
    assert "files" in reason

    ok, reason, _ = verify_output(spec, '{"summary": 1, "files": []}')
    assert ok is False
    assert "类型" in reason

    ok, reason, evidence = verify_output(
        spec, '结果如下\\n```json\\n{"summary": "x", "files": ["a.py"]}\\n```')
    assert ok is True
    assert evidence["json"]["files"] == ["a.py"]


def test_marker_output_requires_the_pattern():
    spec = {"kind": "marker", "pattern": r"VERDICT:\s*PASS"}
    ok, reason, _ = verify_output(spec, "VERDICT: PASS")
    assert ok is True

    ok, reason, _ = verify_output(spec, "VERDICT: FAIL 缺少测试")
    assert ok is False
    assert "缺少测试" in reason

    ok, reason, _ = verify_output(spec, "没有任何结论")
    assert ok is False
    assert "未匹配" in reason


def test_text_output_minimum_length():
    ok, reason, _ = verify_output({"kind": "text", "min_length": 10}, "短")
    assert ok is False
    ok, reason, evidence = verify_output(
        {"kind": "text", "min_length": 3}, "足够长的输出")
    assert ok is True
    assert evidence["length"] >= 3


def test_unknown_kind_is_rejected():
    ok, reason, _ = verify_output({"kind": "wat"}, "x")
    assert ok is False
    assert "unknown" in reason or "未知" in reason


def test_json_output_accepts_plain_object():
    ok, _reason, evidence = verify_output(
        {"kind": "json", "required": ["a"]}, '前缀 {"a": 1} 后缀')
    assert ok is True
    assert evidence["json"] == {"a": 1}
