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
3. **确认 ACP 映射**：默认 `design/review → claude`，其他任务 → `codex`；用户可用 `--acp-map` 覆盖

> `agent_name` 仅供旧 Worker 兼容，不再必填。`service_name` 仅是可选业务上下文。

## Usage

```bash
python3 skills/doc-to-deps/scripts/doc_to_deps.py <input_file> \
  --acp-map '{"backend":"codex","review":"claude"}' \
  [--output <output.json>]
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
    "acp": {"agent": "codex"},
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
python3 skills/doc-to-deps/scripts/doc_to_deps.py README.md \
  --agent-map '{"design":"design-agent","backend":"backend-agent"}' \
  --output deps.json

# Interactive: read from stdin
python3 skills/doc-to-deps/scripts/doc_to_deps.py --interactive \
  --agent-map '{"design":"design-agent","backend":"backend-agent"}'
```
