#!/usr/bin/env python3
"""
design_to_deps.py — dependencies.json 工具：验证、wave 包装。

设计文档 → dependencies.json 的转换由 Claude Code (AI) 完成。
本脚本负责后续的验证和 wave 并行包装。

用法:
  design_to_deps.py deps.json --validate          # 验证
  design_to_deps.py deps.json --wave -o out.json  # 拓扑排序 + wave 包装
  design_to_deps.py deps.json -o out.json         # 透传（仅验证后重新输出）
"""

import argparse
import json
import re
import sys
from pathlib import Path


# ── 类型映射 ──────────────────────────────────────────────────────────────
TYPE_MAP = {
    "backend": {"type": "backend", "capability": "dev"},
    "design": {"type": "design", "capability": "design"},
    "review": {"type": "review", "capability": "review"},
    "test": {"type": "test", "capability": "test"},
    "deploy": {"type": "deploy", "capability": "deploy"},
}


def slugify(text: str) -> str:
    """将中文/英文文本转为 kebab-case slug，≤ 64 字符"""
    s = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    s = re.sub(r"[-\s]+", "-", s)
    return s.strip("-").lower()[:64]


# ── Wave 包装 ──────────────────────────────────────────────────────────────

def wrap_waves(deps: dict) -> dict:
    """
    自动检测并行 wave 并用 parallel/aggregate 节点包装。

    规则:
    - 同一 wave（无相互依赖的任务）→ 一个 parallel 节点
    - wave 之间 → aggregate 节点串联
    """
    if not deps:
        return deps

    remaining = dict(deps)
    waves = []

    while remaining:
        wave = {}
        for name, info in remaining.items():
            deps_satisfied = all(
                d not in remaining for d in info.get("depends_on", [])
            )
            if deps_satisfied and info.get("type") not in ("parallel", "aggregate"):
                wave[name] = info
        if not wave:
            # 死锁：有循环依赖，把剩余的作为最后一波
            waves.append(dict(remaining))
            break
        for name in wave:
            del remaining[name]
        waves.append(wave)

    if len(waves) <= 1:
        return deps

    result = {}
    prev_merge = None

    for i, wave in enumerate(waves):
        parallel_name = f"wave-{i + 1}"
        merge_name = f"wave-{i + 1}-merge"
        children = list(wave.keys())

        parallel_deps = [prev_merge] if prev_merge else []
        result[parallel_name] = {
            "type": "parallel",
            "depends_on": parallel_deps,
            "children": children,
        }

        result[merge_name] = {
            "type": "aggregate",
            "depends_on": [parallel_name],
        }

        for name, info in wave.items():
            result[name] = info

        prev_merge = merge_name

    return result


# ── 验证 ──────────────────────────────────────────────────────────────────

def validate_deps(deps: dict) -> list[str]:
    """验证 dependencies.json 合法性，返回错误列表。"""
    errors = []

    if not isinstance(deps, dict):
        errors.append("顶层必须是 dict/object")
        return errors

    all_names = set(deps.keys())

    for name, info in deps.items():
        if not isinstance(info, dict):
            errors.append(f"{name}: 值必须是 object")
            continue

        node_type = info.get("type", "task")

        if node_type not in ("design", "review", "backend", "test", "deploy",
                              "parallel", "aggregate"):
            errors.append(f"{name}: 未知类型 '{node_type}'")

        if node_type == "parallel":
            children = info.get("children", [])
            if not children:
                errors.append(f"{name}: parallel 节点缺少 children")
            for child in children:
                if child not in all_names:
                    errors.append(f"{name}: child '{child}' 不在 deps 中")

        if node_type not in ("parallel", "aggregate"):
            if not info.get("agent_name"):
                errors.append(f"{name}: 缺少 agent_name")

        for dep in info.get("depends_on", []):
            if isinstance(dep, str) and dep not in all_names:
                errors.append(f"{name}: depends_on '{dep}' 不在 deps 中")
            elif isinstance(dep, dict):
                dep_name = dep.get("task", "")
                if dep_name and dep_name not in all_names:
                    errors.append(f"{name}: depends_on '{dep_name}' 不在 deps 中")

        for dep in info.get("depends_on", []):
            dep_name = dep if isinstance(dep, str) else dep.get("task", "")
            if dep_name == name:
                errors.append(f"{name}: 不能依赖自己")

    return errors


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="dependencies.json 工具：验证、wave 包装 (Harness Framework 兼容)"
    )
    parser.add_argument("input", nargs="?", help="输入文件路径（dependencies.json）")
    parser.add_argument("-o", "--output", help="输出文件路径（用于 --wave 或透传）")
    parser.add_argument("--wave", action="store_true",
                        help="自动检测并行 wave 并用 parallel/aggregate 包装")
    parser.add_argument("--validate", action="store_true",
                        help="仅验证 deps.json，不输出")
    args = parser.parse_args()

    if not args.input:
        print("Error: 请指定输入文件", file=sys.stderr)
        sys.exit(1)

    deps = json.loads(Path(args.input).read_text(encoding="utf-8"))

    # 验证
    errors = validate_deps(deps)
    if errors:
        print(f"验证失败 ({len(errors)} 个错误):")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)

    if args.validate:
        print(f"验证通过: {len(deps)} 个任务")
        return

    # Wave 包装
    if args.wave:
        original_count = len(deps)
        deps = wrap_waves(deps)
        print(f"[wave包装] {original_count} 个任务 → {len(deps)} 个节点")

    # 输出
    output = json.dumps(deps, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"已生成 {args.output} ({len(deps)} 个节点)")
    else:
        print(output)


if __name__ == "__main__":
    main()
