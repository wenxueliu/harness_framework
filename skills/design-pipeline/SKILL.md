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
| `--design` 或 `--deps` | 设计文档路径 或 已有 deps.json | `design.md` |

### 检查流程

1. **如果用户未提供 `req-id`** → 提问用户：
   - "请提供 req-id（需求唯一标识符，如 `REQ-20260502-001`）："
2. **如果用户未提供 `title`** → 提问用户：
   - "请提供需求标题（如 `用户认证功能`）："
3. **如果设计文档路径未提供** → 提问用户：
   - "请提供设计文档路径："
4. 生成 `dependencies.json` 后，检查每个任务的 `service_name` 和 `description` 是否已填写
5. **如果任务的 `service_name` 为空或为默认值 `shared`** → 向用户确认正确的服务名

> **禁止行为**：不得自动生成 `req-id`、`title`、`service_name`。每个值都必须由用户显式提供或确认。

## 两种模式

### 模式 1: AI 辅助提取（推荐）

Claude Code 在设计对话完成后，直接读取设计文档，理解其中的任务分解，然后生成 `dependencies.json`。这是最可靠的方式。

```
设计文档（Markdown）
     │
     ▼
Claude Code 读取 + 理解 → 生成 dependencies.json
     │
     ▼
pipeline.py 验证 + 同步到 Consul
```

### 模式 2: 结构化标记解析

设计文档中嵌入结构化任务标记，脚本自动解析：

```markdown
## 任务分解

<!-- task:backend:user-service -->
### 实现用户注册 API
- 依赖: []
- 描述: 实现 POST /api/v1/users 端点
- 预估: 2h
- 审查: security, logic

<!-- task:backend:user-service -->
### 实现用户登录 API
- 依赖: [实现用户注册 API]
- 描述: 实现 POST /api/v1/login 端点
- 预估: 1.5h
- 审查: security

<!-- task:test:platform -->
### E2E 集成测试
- 依赖: [实现用户注册 API, 实现用户登录 API]
- 描述: 端到端测试注册+登录流程
- 预估: 1h
```

运行:
```bash
python3 skills/design-pipeline/scripts/design_to_deps.py design.md --output deps.json
```

## 使用

### 端到端管道

```bash
# 从设计文档生成 deps 并同步
python3 skills/design-pipeline/scripts/pipeline.py \
  --design design.md \
  --req-id REQ-20260502-001 \
  --title "用户认证功能" \
  --publish
```

### 分步执行

```bash
# 步骤 1: 设计文档 → dependencies.json
python3 skills/design-pipeline/scripts/design_to_deps.py design.md -o deps.json

# 步骤 2: 验证
python3 skills/design-pipeline/scripts/design_to_deps.py --validate deps.json

# 步骤 3: 同步到 Consul
python3 skills/harness-sync/scripts/sync_to_consul.py REQ-001 deps.json --title "标题" --publish
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

## 参考

- 任务标记语法: `references/design-task-template.md`
- deps schema: `references/deps-schema.md`
