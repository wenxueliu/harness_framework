---
name: doc-to-deps
description: |
  Convert any document (markdown, txt, spec file, etc.) into a Harness Framework dependencies.json.
  Use when user wants to parse a document and generate task DAG, or convert spec/PRD to workflow.
  Triggers: "convert document", "parse spec", "generate dependencies", "doc to deps", "extract tasks from"
allowed-tools:
  - Bash
  - Read
  - Write
---

# Doc to Deps Skill

Parse any document and generate a `dependencies.json` for Harness Framework.

## 前置检查：必填参数

在生成 `dependencies.json` 之前，**必须先向用户确认以下信息**。如果缺失，**必须向用户提问并等待用户输入**，不得自动生成。

### 检查流程

1. **确认输入文档**：用户要转换哪个文档？如果未指定，提问：
   - "请提供要转换的文档路径（如 `spec.md`）："
2. **确认 req_id**：如果后续要同步到 Consul，需要 `req_id`。如果用户未指定，提问：
   - "请提供 req_id（需求唯一标识符，如 `req-001`）："
3. **生成后检查**：`dependencies.json` 生成后，检查每个任务的 `service_name` 和 `description`
4. **如果任务的 `service_name` 为空或为推断值** → 向用户确认：
   - "任务 `{task_name}` 的 service_name 当前为 `{value}`，是否正确？如需修改请提供正确的服务名。"

> **禁止行为**：不得自动生成 `service_name`。若无法从文档中提取，必须向用户确认。

## Usage

```bash
python3 skills/doc-to-deps/scripts/doc_to_deps.py <input_file> [--output <output.json>]
```

## Supported Formats

- `.md` / `.markdown` - Markdown documents
- `.txt` - Plain text
- `.json` - JSON (treated as raw spec)
- `.yaml` / `.yml` - YAML (treated as raw spec)

## Output Format

Generates a `dependencies.json` following Harness Framework schema:

```json
{
  "task_name": {
    "type": "backend|design|review|test|deploy",
    "depends_on": [],
    "service_name": "service-name",
    "description": "Task description"
  }
}
```

## Heuristics

- Headers (`#`, `##`) become task candidates
- Bulleted/numbered items become tasks
- Keywords map to types:
  - `design`/`architecture`/`spec` → `design`
  - `review`/`audit`/`check` → `review`
  - `build`/`implement`/`develop`/`coding` → `backend`
  - `test`/`qa`/`verify`/`validate` → `test`
  - `deploy`/`release`/`ship` → `deploy`
- Dependency detection: order of appearance implies dependency chain
- Explicit "depends on" mentions are respected

## Examples

```bash
# Convert README.md to dependencies
python3 skills/doc-to-deps/scripts/doc_to_deps.py README.md --output deps.json

# Interactive: read from stdin
python3 skills/doc-to-deps/scripts/doc_to_deps.py --interactive
```