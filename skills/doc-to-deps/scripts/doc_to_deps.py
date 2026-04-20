#!/usr/bin/env python3
import re
import sys
import json
import argparse
from pathlib import Path


TYPE_KEYWORDS = {
    "design": ["design", "architecture", "spec", "api design", "interface design"],
    "review": ["review", "audit", "check", "review", "evaluate"],
    "backend": ["build", "implement", "develop", "coding", "create api", "write code", "backend"],
    "test": ["test", "qa", "verify", "validate", "e2e", "integration test"],
    "deploy": ["deploy", "release", "ship", "publish", "staging", "production"],
}


def slugify(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", name.lower())
    s = re.sub(r"[-\s]+", "_", s)
    return s.strip("_")[:60]


def infer_type(text: str) -> str:
    text_lower = text.lower()
    for task_type, keywords in TYPE_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return task_type
    return "backend"


def is_meaningful_task(text: str) -> bool:
    if len(text) < 12:
        return False
    skip_patterns = [
        r"^[\d.]+\s+\d", r"^\d+k", r"^#", r"^\(",
        r"[\(\[\{]\d+", r"^\s*[-*]\s*$", r"^\s*$",
    ]
    for p in skip_patterns:
        if re.match(p, text):
            return False
    hex_count = len(re.findall(r"#[\da-f]{3,8}", text, re.I))
    if hex_count >= 2:
        return False
    code_count = text.count("`")
    if code_count > 2 or (code_count == 2 and len(text) < 40):
        return False
    return True


def extract_tasks(content: str) -> list[dict]:
    tasks = []
    lines = content.split("\n")

    current_h1 = None
    current_h2 = None

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
                tasks.append({
                    "text": text,
                    "heading": current_h1,
                    "heading_level": level,
                    "is_heading": True,
                })
            elif level >= 3:
                tasks.append({
                    "text": text,
                    "heading": current_h1 or "General",
                    "heading_level": level,
                    "is_heading": True,
                })
            continue

        bullet = re.match(r"^[-*]\s+(.+)$", line)
        if bullet:
            text = bullet.group(1).strip()
            if text.startswith("[ ]") or text.startswith("- [ ]"):
                text = re.sub(r"^\[[ x]\]\s*", "", text)
            if is_meaningful_task(text):
                tasks.append({
                    "text": text,
                    "heading": current_h1 or current_h2 or "General",
                    "heading_level": 0,
                    "is_heading": False,
                })
            continue

        numbered = re.match(r"^\d+[.)]\s+(.+)$", line)
        if numbered:
            text = numbered.group(1).strip()
            if is_meaningful_task(text):
                tasks.append({
                    "text": text,
                    "heading": current_h1 or current_h2 or "General",
                    "heading_level": 0,
                    "is_heading": False,
                })

    return tasks


def build_deps(tasks: list[dict], hint_deps: list[str] = None) -> dict:
    result = {}
    prev_task_name = None

    for task in tasks:
        is_heading = task.get("is_heading", False)
        heading_level = task.get("heading_level", 0)

        if is_heading and heading_level < 3:
            prev_task_name = None
            continue

        if is_heading and heading_level >= 4:
            prev_task_name = None

        task_name = slugify(task["text"])
        if not task_name or len(task_name) < 4:
            prev_task_name = None
            continue

        service = slugify(task["heading"])
        if not service:
            service = "shared"

        depends_on = []
        if prev_task_name:
            depends_on.append(prev_task_name)

        result[task_name] = {
            "type": infer_type(task["text"]) if not is_heading else "design",
            "depends_on": depends_on,
            "service_name": service,
            "description": task["text"],
        }

        prev_task_name = task_name

    return result


def main():
    parser = argparse.ArgumentParser(description="Convert document to dependencies.json")
    parser.add_argument("input", nargs="?", help="Input file path")
    parser.add_argument("-o", "--output", help="Output file path", default="dependencies.json")
    parser.add_argument("-i", "--interactive", action="store_true", help="Read from stdin")
    args = parser.parse_args()

    if args.interactive:
        content = sys.stdin.read()
    elif args.input:
        content = Path(args.input).read_text(encoding="utf-8")
    else:
        print("Error: specify input file or use --interactive")
        sys.exit(1)

    tasks = extract_tasks(content)
    deps = build_deps(tasks)

    output = json.dumps(deps, ensure_ascii=False, indent=2)
    Path(args.output).write_text(output, encoding="utf-8")
    print(f"Generated {len(deps)} tasks -> {args.output}")


if __name__ == "__main__":
    main()