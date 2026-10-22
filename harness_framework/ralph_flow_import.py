"""Compile a ralph-flow YAML workflow into a Harness job-flow template.

ralph-flow is a step state machine: DO -> independent CHECK -> on_pass/on_fail.
The Harness template model is a DAG, so this compiler keeps one node per step
and maps ``on_pass`` edges to ``depends_on``.  Self-retry (``on_fail`` pointing
back to the same step) and cross-step fallbacks are preserved as metadata and
as explicit instructions in the step description, because a DAG cannot loop.
"""
from __future__ import annotations

import json
import re
from typing import Any

try:  # PyYAML is optional; JSON sources work without it.
    import yaml
except ImportError:  # pragma: no cover - exercised only when PyYAML is absent
    yaml = None


SUPPORTED_PROVIDERS = ("claude", "codex", "opencode")


class RalphFlowImportError(ValueError):
    """Raised when a ralph-flow workflow cannot be compiled."""


def parse_workflow(source: str) -> dict[str, Any]:
    text = (source or "").strip()
    if not text:
        raise RalphFlowImportError("workflow 内容为空")
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RalphFlowImportError(f"workflow JSON 无效: {exc}") from exc
    else:
        if yaml is None:
            raise RalphFlowImportError(
                "解析 YAML 需要安装 PyYAML；也可以直接传入 JSON"
            )
        try:
            data = yaml.safe_load(text)
        except Exception as exc:  # yaml.YAMLError
            raise RalphFlowImportError(f"workflow YAML 无效: {exc}") from exc
    if not isinstance(data, dict):
        raise RalphFlowImportError("workflow 根必须是对象")
    return data


def _manual_steps(workflow: dict[str, Any]) -> set[str]:
    raw = workflow.get("manual_step") or []
    if isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",")]
    if not isinstance(raw, list):
        raise RalphFlowImportError("manual_step 必须是数组或逗号分隔字符串")
    return {str(item).strip() for item in raw if str(item).strip()}


def _checks(step: dict[str, Any]) -> list[str]:
    voting = step.get("check_voting")
    if voting is not None:
        if not isinstance(voting, list) or not voting:
            raise RalphFlowImportError(
                f"步骤 {step.get('id')} 的 check_voting 必须是非空数组"
            )
        result = []
        for entry in voting:
            if not isinstance(entry, dict) or not str(entry.get("check") or "").strip():
                raise RalphFlowImportError(
                    f"步骤 {step.get('id')} 的 check_voting 缺少 check"
                )
            result.append(str(entry["check"]).strip())
        return result
    check = step.get("check")
    if check is None or not str(check).strip():
        return []
    return [str(check).strip()]


def _description(step: dict[str, Any], workflow: dict[str, Any]) -> str:
    step_id = step["id"]
    on_fail = step["on_fail"]
    max_fail = int(step.get("max_fail_count") or 1)
    lines = [
        f"Ralph-Flow 工作流步骤：{step_id} — {step.get('desc', '')}".strip(),
        "",
        "DO:",
        str(step["do"]).strip(),
        "",
        f"输入：{str(step.get('input', '')).strip()}",
        f"产出：{str(step.get('output', '')).strip()}",
        f"产物目录：当前工作目录下的 .ralph-flow-artifacts/{step_id}/（需要时创建）。",
    ]
    checks = _checks(step)
    if checks:
        lines.extend(["", "CHECK:"])
        lines.extend(f"- {item}" for item in checks)
    lines.extend([
        "",
        f"失败处理：自检不通过时按 on_fail={on_fail} 处理，最多 {max_fail} 次。",
        "用户输入参数见提示词 JSON 的 parameters 字段（例如 requirement / task）。",
        "只有真实通过 CHECK 才能结束；不要只描述计划，也不要在验证失败时声称完成。",
    ])
    if on_fail != step_id:
        lines.append(
            f"注意：当前框架按 DAG 执行，不会自动回退到其他步骤；"
            f"请在本步骤内修正，或明确报告需要回到步骤 {on_fail}。"
        )
    return "\n".join(lines)


def _check_description(step_id: str, desc: str, criteria: str) -> str:
    return "\n".join([
        f"Ralph-Flow 工作流验证节点：{step_id} — {desc}",
        "",
        "你是独立验证者，没有参与上面的实现，只依据工作区里的实际内容判断。",
        f"验收标准:\n{criteria or '（该步骤没有显式验收标准，请确认产出存在且完整）'}",
        "",
        "检查方式: 阅读产出文件、查看代码、运行必要的验证命令。不要相信任何"
        "\"已完成\"的声明。",
        "",
        "最后一行必须且只能输出一行结论:",
        "VERDICT: PASS",
        "或者",
        "VERDICT: FAIL <未通过的原因>",
    ])


_FILE_OUTPUT = re.compile(r"[\w./-]+\.[A-Za-z0-9]{1,5}")


def _derive_output_format(output: str) -> dict[str, Any]:
    """产物是文件名时用文件校验，否则不声明格式。"""
    match = _FILE_OUTPUT.search(output or "")
    if not match:
        return {}
    return {"kind": "file", "path": match.group(0)}


def _check_instruction(criteria: str, system_prompt: str = "") -> str:
    lines = []
    if system_prompt:
        lines.extend(["额外要求:", system_prompt, ""])
    lines.extend([
        "你是独立验证者，没有参与上面的实现，只依据工作区里的实际内容判断。",
        f"验收标准:\n{criteria or '（该步骤没有显式验收标准，请确认产出存在且完整）'}",
        "检查方式: 阅读产出文件、查看代码、运行必要的验证命令。不要相信任何"
        "\"已完成\"的声明。",
        "最后一行必须且只能输出一行结论: VERDICT: PASS 或 VERDICT: FAIL <原因>。",
    ])
    return "\n".join(lines)



def _provenance(
    workflow: dict[str, Any], steps: list[dict[str, Any]], manual: set[str],
) -> dict[str, Any]:
    """模板级溯源：足够把编译结果还原成 ralph-flow YAML。"""
    return {
        "source": "ralph-flow",
        "workflow": str(workflow.get("name") or ""),
        "description": str(workflow.get("description") or ""),
        "manual_step": sorted(manual),
        "auto_reset": bool(workflow.get("auto_reset")),
        "adversarial_check": workflow.get("adversarial_check"),
        "steps": [{
            "id": step["id"],
            "desc": str(step.get("desc") or ""),
            "do": str(step.get("do") or ""),
            "input": str(step.get("input") or ""),
            "output": str(step.get("output") or ""),
            "check": step.get("check"),
            "check_voting": step.get("check_voting"),
            "on_pass": str(step["on_pass"]),
            "on_fail": str(step["on_fail"]),
            "max_fail_count": int(step["max_fail_count"]),
            "reset": bool(step.get("reset") or workflow.get("auto_reset")),
        } for step in steps],
    }


def compile_workflow(
    workflow: dict[str, Any],
    *,
    name: str = "",
    agent: str = "opencode",
    check_agent: str = "",
    task_type: str = "backend",
) -> dict[str, Any]:
    """Return a template body accepted by ``JobFlowService.create_template``.

    Every ralph-flow step lowers to two DAG nodes: ``<step>`` runs the DO
    phase, ``<step>__check`` verifies it in an independent session.  ``on_pass``
    becomes a dependency edge; ``on_fail`` becomes the check node's fallback
    target, which the framework uses to re-run the upstream node.
    """
    steps = workflow.get("steps")
    if not isinstance(steps, list) or not steps:
        raise RalphFlowImportError("workflow.steps 必须是非空数组")
    manual = _manual_steps(workflow)
    ids: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    for step in steps:
        if not isinstance(step, dict):
            raise RalphFlowImportError("每个 step 必须是对象")
        step_id = str(step.get("id") or "").strip()
        if not step_id:
            raise RalphFlowImportError("step.id 不能为空")
        if step_id in by_id:
            raise RalphFlowImportError(f"step.id 重复: {step_id}")
        if step.get("workflow"):
            raise RalphFlowImportError(
                f"步骤 {step_id} 使用了子工作流 workflow:，暂不支持导入"
            )
        for field in ("desc", "do", "input", "output", "on_pass", "on_fail",
                      "max_fail_count"):
            if step.get(field) in {None, ""}:
                raise RalphFlowImportError(f"步骤 {step_id} 缺少必填字段 {field}")
        try:
            if int(step["max_fail_count"]) < 1:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise RalphFlowImportError(
                f"步骤 {step_id} 的 max_fail_count 必须是 >= 1 的整数"
            ) from exc
        ids.append(step_id)
        by_id[step_id] = step
    for step in steps:
        for field in ("on_pass", "on_fail"):
            target = str(step[field])
            if field == "on_pass" and target == "done":
                continue
            if target not in by_id:
                raise RalphFlowImportError(
                    f"步骤 {step['id']} 的 {field} 引用了不存在的步骤 {target}"
                )
    if manual - set(ids):
        raise RalphFlowImportError(
            "manual_step 引用了不存在的步骤: " + ", ".join(sorted(manual - set(ids)))
        )

    # 每个 ralph 步骤降成两个 DAG 节点：<step>（DO）与 <step>__check（CHECK）。
    # on_pass 决定 check → 下一步 do 的依赖；on_fail 记录为 check 节点的回退目标，
    # 由框架在 CHECK 失败时重置上游 do 节点（不是 DAG 边，因此不产生环）。
    check_nodes: dict[str, list[str]] = {}
    for step in steps:
        step_id = step["id"]
        voting = step.get("check_voting") or []
        if voting:
            check_nodes[step_id] = [
                f"{step_id}__check{index + 1}" for index in range(len(voting))
            ]
        else:
            check_nodes[step_id] = [f"{step_id}__check"]

    do_dependencies: dict[str, list[str]] = {step_id: [] for step_id in ids}
    for step in steps:
        target = str(step["on_pass"])
        if target == "done":
            continue
        do_dependencies[target].extend(check_nodes[step["id"]])

    tasks: list[dict[str, Any]] = []
    for step in steps:
        step_id = step["id"]
        # 语义全部落在节点的 do/input/output/on_fail 等契约字段上，
        # 节点里不再保留 ralph 专用字段；溯源信息放在模板级 provenance。
        task: dict[str, Any] = {
            "id": step_id,
            "name": f"{step.get('desc') or step_id}（执行）",
            "type": task_type,
            "agent": agent,
            "depends_on": do_dependencies[step_id],
            "description": _description(step, workflow),
            "do": str(step.get("do") or "").strip(),
            "input": str(step.get("input") or "").strip(),
            "output": str(step.get("output") or "").strip(),
        }
        output_format = _derive_output_format(task["output"])
        if output_format:
            task["output_format"] = output_format
        tasks.append(task)
        voting = step.get("check_voting") or []
        criteria_list = [
            str(entry.get("check") or "") for entry in voting
            if isinstance(entry, dict)
        ]
        if not criteria_list and step.get("check"):
            criteria_list = [str(step["check"])]
        for index, check_id in enumerate(check_nodes[step_id]):
            criteria = criteria_list[index] if index < len(criteria_list) else ""
            adversarial = workflow.get("adversarial_check")
            adversarial = adversarial if isinstance(adversarial, dict) else {}
            verifier = str(adversarial.get("agent") or "").strip().lower()
            if verifier not in SUPPORTED_PROVIDERS:
                verifier = ""
            system_prompt = str(adversarial.get("system_prompt") or "").strip()
            check_task: dict[str, Any] = {
                "id": check_id,
                "name": f"{step.get('desc') or step_id}（验证{index + 1 if voting else ''}）",
                "type": task_type,
                "agent": verifier or check_agent or agent,
                "depends_on": [step_id],
                "description": _check_description(
                    step_id, step.get("desc") or step_id, criteria,
                ),
                "do": _check_instruction(criteria, system_prompt),
                "input": str(step.get("output") or "").strip(),
                "output": "验证结论：最后一行输出 VERDICT: PASS",
                "output_format": {"kind": "marker", "pattern": r"VERDICT:\s*PASS"},
                "check_criteria": criteria,
                "require_verdict": True,
                "require_approval": step_id in manual,
                "on_fail": {
                    "task": str(step["on_fail"]),
                    "max_fail_count": int(step["max_fail_count"]),
                },
                # 同一��骤的多个验证者共享失败预算（对应 ralph 的步骤级 fail_count）
                "fail_group": step_id,
            }
            if adversarial:
                # 独立验证者：可以读文件、跑命令，但不能改工作区。
                check_task["acp"] = {"permission_policy": "deny_edit"}
            tasks.append(check_task)
    return {
        "name": name or str(workflow.get("name") or "ralph-flow"),
        "description": str(workflow.get("description") or ""),
        "tasks": tasks,
        "provenance": _provenance(workflow, steps, manual),
        # 实例创建表单里的用户任务输入；步骤通过提示词 JSON 的 parameters 字段读取。
        "parameter_schema": {
            "type": "object",
            "properties": {
                "requirement": {"type": "string", "title": "用户任务描述"},
            },
            "required": [],
        },
    }

