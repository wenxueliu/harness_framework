#!/usr/bin/env python3
"""
design_to_deps.py — 从设计文档提取任务 DAG，生成 dependencies.json

支持两种输入模式:
1. 结构化标记模式: 设计文档中嵌入 <!-- task:type:service --> 标记
2. 启发式模式: 从 Markdown 标题和列表推断任务（兼容原 doc_to_deps.py）

输出: 与 Aggregator 兼容的平铺 dict 格式 dependencies.json

用法:
  design_to_deps.py design.md --output deps.json
  design_to_deps.py design.md --output deps.json --wave-parallel   # 自动包装 wave
  design_to_deps.py --validate deps.json                            # 验证
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

REVIEWER_MAP = {
    "security": "security",
    "logic": "logic",
    "performance": "performance",
}


def slugify(text: str) -> str:
    """将中文/英文文本转为 kebab-case slug，≤ 64 字符"""
    # 保留中文、字母、数字、空格、连字符
    s = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    s = re.sub(r"[-\s]+", "-", s)
    return s.strip("-").lower()[:64]


# ── 模式 1: 结构化标记解析 ─────────────────────────────────────────────────

TASK_MARKER_RE = re.compile(
    r"<!--\s*task\s*:\s*(\w+)\s*(?::\s*([\w_.-]+))?\s*-->"
)

DEP_LIST_RE = re.compile(r"-\s*依赖\s*:\s*\[(.*?)\]")
DEP_DEPS_RE = re.compile(r"-\s*(?:depends_on|deps)\s*:\s*\[(.*?)\]", re.I)
DESC_RE = re.compile(r"-\s*(?:描述|description)\s*:\s*(.+)", re.I)
EST_RE = re.compile(r"-\s*(?:预估|estimate|estimated)\s*:\s*([\d.]+)\s*h", re.I)
REVIEW_RE = re.compile(r"-\s*(?:审查|review|reviewers)\s*:\s*(.+)", re.I)
CAPABILITY_RE = re.compile(r"-\s*(?:能力|capability)\s*:\s*(\w+)", re.I)
BLOCKING_RE = re.compile(r"-\s*(?:阻塞|blocking)\s*:\s*(true|false)", re.I)
PRIORITY_RE = re.compile(r"-\s*(?:优先级|priority)\s*:\s*(\d+)", re.I)


def parse_task_markers(content: str) -> list[dict]:
    """
    从设计文档中提取 <!-- task:type:service --> 标记的任务。

    格式: marker 后紧跟 ### 标题行，再是属性列表。
    返回 raw task 列表，每个元素包含:
      title, type, service_name, depends_on_titles, description,
      estimated_hours, reviewers, capability, blocking, priority
    """
    lines = content.split("\n")
    tasks = []
    current_task = None
    lines_since_marker = 0

    for line in lines:
        # 检测 task marker（必须在 heading 之前检查到达 marker 的 heading）
        marker_match = TASK_MARKER_RE.search(line)
        if marker_match:
            # 保存上一个 task
            if current_task:
                tasks.append(current_task)

            task_type = marker_match.group(1)
            service_name = marker_match.group(2) or "shared"

            current_task = {
                "title": "",  # 由紧随其后的 ### 标题设置
                "type": task_type,
                "service_name": service_name,
                "depends_on_titles": [],
                "description": "",
                "estimated_hours": None,
                "reviewers": [],
                "capability": None,
                "blocking": True,
                "priority": 0,
            }
            lines_since_marker = 0
            continue

        # 检测 heading
        heading_match = re.match(r"^(#{1,4})\s+(.+)$", line)
        if heading_match:
            heading_text = heading_match.group(2).strip()
            heading_level = len(heading_match.group(1))

            # 如果当前 task 还没有 title，用这个 heading 作为 title
            if current_task and not current_task["title"]:
                current_task["title"] = heading_text
                continue
            # 如果是 h2+ 的非任务标题，重置为安全状态
            elif heading_level <= 2 and current_task and current_task["title"]:
                # h2 可能是下一个章节的开始，保存当前 task
                tasks.append(current_task)
                current_task = None
            continue

        if current_task is None:
            continue

        lines_since_marker += 1

        # 依赖列表
        dep_match = DEP_LIST_RE.search(line)
        if not dep_match:
            dep_match = DEP_DEPS_RE.search(line)
        if dep_match:
            deps_text = dep_match.group(1).strip()
            if deps_text:
                current_task["depends_on_titles"] = [
                    d.strip() for d in deps_text.split(",") if d.strip()
                ]

        # 描述
        desc_match = DESC_RE.search(line)
        if desc_match:
            current_task["description"] = desc_match.group(1).strip()

        # 预估时间
        est_match = EST_RE.search(line)
        if est_match:
            current_task["estimated_hours"] = float(est_match.group(1))

        # 审查者
        review_match = REVIEW_RE.search(line)
        if review_match:
            current_task["reviewers"] = [
                r.strip() for r in review_match.group(1).split(",") if r.strip()
            ]

        # 能力覆盖
        cap_match = CAPABILITY_RE.search(line)
        if cap_match:
            current_task["capability"] = cap_match.group(1).strip()

        # blocking
        block_match = BLOCKING_RE.search(line)
        if block_match:
            current_task["blocking"] = block_match.group(1).lower() == "true"

        # priority
        pri_match = PRIORITY_RE.search(line)
        if pri_match:
            current_task["priority"] = int(pri_match.group(1))

    # 保存最后一个 task
    if current_task:
        tasks.append(current_task)

    return tasks


def resolve_task_names(raw_tasks: list[dict]) -> dict:
    """
    将 depends_on_titles（任务标题引用）解析为 depends_on（task name slug 引用）。
    返回 ready-to-use deps dict。
    """
    # 建立 title → slug 映射
    title_to_slug = {}
    for t in raw_tasks:
        if t["title"]:
            title_to_slug[t["title"]] = slugify(t["title"])

    deps = {}
    for t in raw_tasks:
        slug = slugify(t["title"]) if t["title"] else f"task-{len(deps):03d}"

        # 去重: 同名 slug 追加数字
        base_slug = slug
        counter = 1
        while slug in deps:
            slug = f"{base_slug}-{counter}"
            counter += 1

        # 解析依赖: title → slug
        depends_on = []
        for dep_title in t.get("depends_on_titles", []):
            dep_slug = title_to_slug.get(dep_title)
            if dep_slug:
                depends_on.append(dep_slug)
            # 也可能直接引用了 slug
            elif dep_title in deps:
                depends_on.append(dep_title)
            else:
                # 尝试 slugify
                maybe_slug = slugify(dep_title)
                depends_on.append(maybe_slug)

        # 类型默认值
        type_info = TYPE_MAP.get(t["type"], {"type": "backend", "capability": "dev"})

        task_def = {
            "type": type_info["type"],
            "depends_on": depends_on,
            "service_name": t["service_name"],
            "description": t["description"] or t["title"],
            "capability": t.get("capability") or type_info["capability"],
        }

        if not t.get("blocking", True):
            task_def["blocking"] = False
        if t.get("priority", 0) != 0:
            task_def["priority"] = t["priority"]

        # metadata
        metadata = {}
        if t.get("estimated_hours"):
            metadata["estimated_hours"] = t["estimated_hours"]
        if t.get("reviewers"):
            metadata["review_requirements"] = [
                {"reviewer": r, "reason": ""} for r in t["reviewers"]
            ]
        if metadata:
            task_def["metadata"] = metadata

        deps[slug] = task_def

    return deps


# ── 模式 2: 启发式解析（兼容原 doc_to_deps.py） ─────────────────────────────

TYPE_KEYWORDS = {
    "design": ["design", "architecture", "spec", "api design", "interface design",
               "设计", "架构", "规范", "契约"],
    "review": ["review", "audit", "check", "evaluate", "评审", "审查", "审计"],
    "backend": ["build", "implement", "develop", "coding", "create api", "write code",
                "实现", "开发", "构建", "编写"],
    "test": ["test", "qa", "verify", "validate", "e2e", "integration test",
             "测试", "验证", "端到端"],
    "deploy": ["deploy", "release", "ship", "publish", "部署", "发布"],
}


def is_meaningful_task(text: str) -> bool:
    if len(text) < 8:
        return False
    skip_patterns = [
        r"^[\d.]+\s+\d", r"^\d+k", r"^#", r"^\(",
        r"^\s*[-*]\s*$", r"^\s*$",
    ]
    for p in skip_patterns:
        if re.match(p, text):
            return False
    return True


def infer_type(text: str) -> str:
    text_lower = text.lower()
    for task_type, keywords in TYPE_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return task_type
    return "backend"


def extract_heuristic(content: str) -> dict:
    """启发式解析: 从 Markdown 结构推断任务。"""
    lines = content.split("\n")
    tasks_raw = []
    current_h1 = None
    current_h2 = None
    prev_task_name = None

    for line in lines:
        heading = re.match(r"^(#{1,4})\s+(.+)$", line)
        if heading:
            level = len(heading.group(1))
            text = heading.group(2).strip().rstrip(":")
            if level == 1:
                current_h1 = text
                current_h2 = None
            elif level == 2:
                current_h2 = text
                current_h1 = current_h1 or "General"
                prev_task_name = None  # h2 resets sequential chain
            continue

        # 列表项
        bullet = re.match(r"^[-*]\s+(.+)$", line)
        if bullet:
            text = bullet.group(1).strip()
            text = re.sub(r"^\[[ x]\]\s*", "", text)
            if not is_meaningful_task(text):
                continue
            slug = slugify(text)
            if len(slug) < 4:
                continue
            service = slugify(current_h1 or current_h2 or "general")
            deps_list = [prev_task_name] if prev_task_name else []
            tasks_raw.append({
                "slug": slug,
                "type": infer_type(text),
                "service_name": service,
                "depends_on": deps_list,
                "description": text,
            })
            prev_task_name = slug
            continue

        # 编号列表
        numbered = re.match(r"^\d+[.)]\s+(.+)$", line)
        if numbered:
            text = numbered.group(1).strip()
            if not is_meaningful_task(text):
                continue
            slug = slugify(text)
            if len(slug) < 4:
                continue
            service = slugify(current_h1 or current_h2 or "general")
            deps_list = [prev_task_name] if prev_task_name else []
            tasks_raw.append({
                "slug": slug,
                "type": infer_type(text),
                "service_name": service,
                "depends_on": deps_list,
                "description": text,
            })
            prev_task_name = slug

    # 构建 deps dict
    deps = {}
    for t in tasks_raw:
        type_info = TYPE_MAP.get(t["type"], {"type": "backend", "capability": "dev"})
        deps[t["slug"]] = {
            "type": type_info["type"],
            "depends_on": t["depends_on"],
            "service_name": t["service_name"],
            "description": t["description"],
            "capability": type_info["capability"],
        }

    return deps


# ── Wave 包装 ──────────────────────────────────────────────────────────────

def wrap_waves(deps: dict) -> dict:
    """
    自动检测并行 wave 并用 parallel/aggregate 节点包装。

    规则:
    - 同一 wave（无相互依赖的任务）→ 一个 parallel 节点
    - wave 之间 → aggregate 节点串联
    - 最后有 E2E 任务 → 依赖最后一个 aggregate
    """
    if not deps:
        return deps

    # 拓扑排序分 wave —— 先识别整个 wave，再一次性移除
    # 避免循环内删除导致 cascading activation
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
        # 一次性移除整个 wave
        for name in wave:
            del remaining[name]
        waves.append(wave)

    # 打包
    if len(waves) <= 1:
        return deps

    result = {}
    prev_merge = None

    for i, wave in enumerate(waves):
        parallel_name = f"wave-{i + 1}"
        merge_name = f"wave-{i + 1}-merge"
        children = list(wave.keys())

        # parallel 节点
        parallel_deps = [prev_merge] if prev_merge else []
        result[parallel_name] = {
            "type": "parallel",
            "depends_on": parallel_deps,
            "children": children,
        }

        # aggregate 节点
        result[merge_name] = {
            "type": "aggregate",
            "depends_on": [parallel_name],
        }

        # 子任务保持原样
        for name, info in wave.items():
            result[name] = info

        prev_merge = merge_name

        # 如果只有一个任务且是 test 类型，可以不包装
        # （保持用户显式声明的依赖关系）

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

        # type 必填
        if node_type not in ("design", "review", "backend", "test", "deploy",
                              "parallel", "aggregate"):
            errors.append(f"{name}: 未知类型 '{node_type}'")

        # parallel 必须有 children
        if node_type == "parallel":
            children = info.get("children", [])
            if not children:
                errors.append(f"{name}: parallel 节点缺少 children")
            for child in children:
                if child not in all_names:
                    errors.append(f"{name}: child '{child}' 不在 deps 中")

        # task 必须有 service_name
        if node_type not in ("parallel", "aggregate"):
            if not info.get("service_name"):
                errors.append(f"{name}: 缺少 service_name")

        # depends_on 引用检查
        for dep in info.get("depends_on", []):
            if isinstance(dep, str) and dep not in all_names:
                errors.append(f"{name}: depends_on '{dep}' 不在 deps 中")
            elif isinstance(dep, dict):
                dep_name = dep.get("task", "")
                if dep_name and dep_name not in all_names:
                    errors.append(f"{name}: depends_on '{dep_name}' 不在 deps 中")

        # 不能依赖自己
        for dep in info.get("depends_on", []):
            dep_name = dep if isinstance(dep, str) else dep.get("task", "")
            if dep_name == name:
                errors.append(f"{name}: 不能依赖自己")

    return errors


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="从设计文档生成 dependencies.json (Harness Framework 兼容)"
    )
    parser.add_argument("input", nargs="?", help="输入文件路径（设计文档 .md）")
    parser.add_argument("-o", "--output", default="dependencies.json", help="输出文件路径")
    parser.add_argument("--wave", action="store_true",
                        help="自动检测并行 wave 并用 parallel/aggregate 包装")
    parser.add_argument("--validate", action="store_true",
                        help="仅验证现有 deps.json，不生成")
    parser.add_argument("--mode", choices=["auto", "marker", "heuristic"],
                        default="auto", help="解析模式（默认 auto: 先 marker 后 heuristic）")
    args = parser.parse_args()

    # 验证模式
    if args.validate:
        if not args.input:
            print("Error: --validate 需要指定输入文件", file=sys.stderr)
            sys.exit(1)
        deps = json.loads(Path(args.input).read_text(encoding="utf-8"))
        errors = validate_deps(deps)
        if errors:
            print(f"验证失败 ({len(errors)} 个错误):")
            for e in errors:
                print(f"  ✗ {e}")
            sys.exit(1)
        else:
            print(f"验证通过: {len(deps)} 个任务")
            return
        return

    if not args.input:
        print("Error: 请指定输入文件或使用 --validate", file=sys.stderr)
        sys.exit(1)

    content = Path(args.input).read_text(encoding="utf-8")

    # 自动检测模式
    if args.mode == "auto":
        # 先尝试结构化标记
        raw_tasks = parse_task_markers(content)
        if raw_tasks:
            deps = resolve_task_names(raw_tasks)
            print(f"[marker模式] 提取到 {len(deps)} 个标记任务")
        else:
            # 回退到启发式
            deps = extract_heuristic(content)
            print(f"[heuristic模式] 推断出 {len(deps)} 个任务")
    elif args.mode == "marker":
        raw_tasks = parse_task_markers(content)
        deps = resolve_task_names(raw_tasks)
        print(f"[marker模式] 提取到 {len(deps)} 个标记任务")
    else:
        deps = extract_heuristic(content)
        print(f"[heuristic模式] 推断出 {len(deps)} 个任务")

    if not deps:
        print("Error: 未找到任何任务。请检查输入文件格式。", file=sys.stderr)
        print("参考: skills/design-pipeline/references/design-task-template.md", file=sys.stderr)
        sys.exit(1)

    # 自动 wave 包装
    if args.wave:
        deps = wrap_waves(deps)
        print(f"[wave包装] 最终 {len(deps)} 个节点")

    # 验证
    errors = validate_deps(deps)
    if errors:
        print(f"警告: 验证发现 {len(errors)} 个问题:")
        for e in errors:
            print(f"  ✗ {e}")

    # 写入
    output = json.dumps(deps, ensure_ascii=False, indent=2)
    Path(args.output).write_text(output, encoding="utf-8")
    print(f"已生成 {args.output} ({len(deps)} 个节点)")

    if errors:
        print("请修正上述问题后重新生成。")


if __name__ == "__main__":
    main()
