"""产物格式校验。

节点可以声明 ``output_format``，框架在 Agent 结束这一轮后按格式校验产物；
不符合就视为该节点失败（配合 ``on_fail`` 触发回退）。

支持的 kind：

``file``    产物必须是工作目录下的文件（也接受 .ralph-flow-artifacts/<task>/ 等产物目录）
``json``    Agent 最后必须给出一段 JSON，按 required/types 校验
``marker``  Agent 输出里必须匹配给定的正则
``text``    Agent 输出长度下限
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

_ARTIFACT_DIRS = (
    ".ralph-flow-artifacts/{task_id}",
    ".harness/artifacts/{task_id}",
)


def verify_output(
    spec: dict[str, Any], text: str, cwd: str = "", task_id: str = "",
) -> tuple[bool, str, dict[str, Any]]:
    """Return ``(ok, reason, evidence)`` for the produced output."""
    kind = str(spec.get("kind") or "text")
    if kind == "file":
        return _verify_file(spec, cwd, task_id)
    if kind == "json":
        return _verify_json(spec, text or "")
    if kind == "marker":
        return _verify_marker(spec, text or "")
    if kind == "text":
        return _verify_text(spec, text or "")
    return False, f"未知的 output_format.kind: {kind}", {}


def _verify_file(
    spec: dict[str, Any], cwd: str, task_id: str,
) -> tuple[bool, str, dict[str, Any]]:
    path = str(spec.get("path") or "").strip()
    if not path:
        return False, "output_format 缺少 path", {}
    min_bytes = int(spec.get("min_bytes") or 1)
    candidates = [path]
    for pattern in _ARTIFACT_DIRS:
        candidates.append(pattern.format(task_id=task_id) + "/" + path)
    for candidate in candidates:
        full = os.path.join(cwd, candidate) if cwd else candidate
        if not os.path.isfile(full):
            continue
        size = os.path.getsize(full)
        if size < min_bytes:
            return False, f"产物 {candidate} 为空（{size} < {min_bytes} 字节）", {
                "path": candidate, "bytes": size,
            }
        return True, "", {"path": candidate, "bytes": size}
    return False, f"没有找到产物文件 {path}（工作目录 {cwd or '.'}）", {
        "candidates": candidates,
    }


def _last_json_object(text: str) -> dict[str, Any] | None:
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = blocks or []
    start = text.rfind("{")
    if start != -1:
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:index + 1])
                    break
    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _verify_json(
    spec: dict[str, Any], text: str,
) -> tuple[bool, str, dict[str, Any]]:
    parsed = _last_json_object(text)
    if parsed is None:
        return False, "输出里没有找到 JSON 对象", {}
    required = [str(item) for item in (spec.get("required") or [])]
    missing = [key for key in required if key not in parsed]
    if missing:
        return False, f"JSON 缺少字段: {', '.join(missing)}", {"json": parsed}
    types = spec.get("types") if isinstance(spec.get("types"), dict) else {}
    for key, expected in types.items():
        if key not in parsed:
            continue
        value = parsed[key]
        checks = {
            "string": lambda item: isinstance(item, str),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "array": lambda item: isinstance(item, list),
            "object": lambda item: isinstance(item, dict),
        }
        checker = checks.get(str(expected))
        if checker and not checker(value):
            return False, f"JSON 字段 {key} 类型必须是 {expected}", {"json": parsed}
    return True, "", {"json": parsed}


def _verify_marker(
    spec: dict[str, Any], text: str,
) -> tuple[bool, str, dict[str, Any]]:
    pattern = str(spec.get("pattern") or "").strip()
    if not pattern:
        return False, "output_format 缺少 pattern", {}
    try:
        matcher = re.compile(pattern)
    except re.error as exc:
        return False, f"output_format.pattern 无效: {exc}", {}
    if matcher.search(text):
        return True, "", {"matched": matcher.search(text).group(0)}
    reason = ""
    fail = re.search(r"VERDICT:\s*FAIL\s*(.*)", text, re.IGNORECASE)
    if fail:
        reason = fail.group(1).strip()
    detail = f"未匹配 {pattern}"
    if reason:
        detail = f"{detail}（{reason}）"
    return False, detail, {}


def _verify_text(
    spec: dict[str, Any], text: str,
) -> tuple[bool, str, dict[str, Any]]:
    min_length = int(spec.get("min_length") or 0)
    if len(text.strip()) < min_length:
        return False, f"输出长度 {len(text.strip())} 小于要求的 {min_length}", {
            "length": len(text.strip()),
        }
    return True, "", {"length": len(text.strip())}
