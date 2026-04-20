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