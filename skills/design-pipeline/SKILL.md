---
name: design-pipeline
description: |
  设计管道：从设计文档提取任务 DAG，生成 dependencies.json，同步到 Consul。
  打通设计仓→Consul 的最后一公里。
  Use when: 设计完成后要生成任务依赖图、同步到 Consul、或从设计文档自动创建 workflow。
  Triggers: "生成任务", "同步到 Consul", "创建 workflow", "设计完成", "design to deps",
  "pipeline", "依赖图", "dependencies.json"
---

# Design Pipeline Skill

将设计文档（中文/英文）转换为 Harness Framework 可调度的 `dependencies.json`，并同步到 Consul。

## 前置检查：必填参数

在运行 pipeline 之前，**必须先确认以下参数**。如果缺失，**必须向用户提问并等待用户输入**，不得自动生成。

### 必填参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `--req-id` | 需求唯一标识符 | `REQ-20260502-001` |
| `--title` | 需求标题 | `"用户认证功能"` |
| `--deps` | 已生成的 dependencies.json 路径 | `deps.json` |

### 检查流程

1. **如果用户未提供 `req-id`** → 提问用户：
   - "请提供 req-id（需求唯一标识符，如 `REQ-20260502-001`）："
2. **如果用户未提供 `title`** → 提问用户：
   - "请提供需求标题（如 `用户认证功能`）："
3. 生成 `dependencies.json` 后，检查每个任务的 `service_name` 和 `description` 是否已填写
4. **如果任务的 `service_name` 为空或为默认值 `shared`** → 向用户确认正确的服务名

> **禁止行为**：不得自动生成 `req-id`、`title`、`service_name`。每个值都必须由用户显式提供或确认。

## 工作流：AI 辅助提取

Claude Code 在设计对话完成后，直接读取设计文档，理解其中的任务分解，然后生成 `dependencies.json`。这是唯一支持的方式。

```
设计文档（Markdown）
     │
     ▼
Claude Code 读取 + 理解 → 生成 dependencies.json
     │
     ▼
pipeline.py 验证 + 同步到 Consul
```

### 步骤 1: 生成 dependencies.json

Claude Code 读取设计文档后，直接生成符合 schema 的 `dependencies.json`。生成时遵循以下规则：

- 从设计文档中识别任务的分解结构
- 确定每个任务的类型（`backend`/`design`/`review`/`test`/`deploy`）
- 确定任务间的依赖关系
- 确定每个任务归属的服务（`service_name`）
- **禁止自动生成 `service_name`**：如果无法确定服务归属，向用户确认

### 步骤 2: 验证

```bash
python3 skills/design-pipeline/scripts/design_to_deps.py --validate deps.json
```

### 步骤 3: 同步到 Consul

```bash
python3 skills/design-pipeline/scripts/pipeline.py \
  --deps dependencies.json \
  --req-id REQ-20260502-001 \
  --title "用户认证功能" \
  --publish
```

## dependencies.json 格式

产出与 Aggregator 兼容的平铺 dict 格式:

```json
{
  "task-name": {
    "type": "backend|design|review|test|deploy|parallel|aggregate",
    "depends_on": ["upstream-task"],
    "service_name": "user-service",
    "description": "任务描述",
    "capability": "dev|design|test|review",
    "blocking": true,
    "children": ["child-1", "child-2"],
    "metadata": {
      "estimated_hours": 2,
      "review_requirements": [{"reviewer": "security", "reason": "涉及认证"}],
      "test_bindings": {"ut_cases": ["UT-1"], "api_cases": ["API-1"]}
    }
  }
}
```

## 任务类型

| Type | Capability | 谁认领 |
|------|-----------|--------|
| `design` | `design` | 设计 Agent（设计仓 Claude） |
| `review` | `review` | 审查 Agent |
| `backend` | `dev` | 服务仓 Claude（按 service_name 匹配） |
| `test` | `test` | 测试 Agent |
| `deploy` | `deploy` | 部署 Agent |
| `parallel` | — | Aggregator 自动展开子任务 |
| `aggregate` | — | Aggregator 自动等待子任务全部完成 |

## Wave 并行包装

对于有并行任务的设计，可以用 `--wave` 自动包装：

```bash
python3 skills/design-pipeline/scripts/design_to_deps.py deps.json --wave -o deps_waved.json
```

这会对任务做拓扑排序，将无相互依赖的任务归入同一 wave，并用 `parallel`/`aggregate` 节点包装。

## 参考

- deps schema: `references/deps-schema.md`
