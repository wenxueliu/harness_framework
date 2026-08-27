---
name: harness-sync
description: |
  Sync workflow tasks to Consul KV for the Harness Framework. Create new requirements (req_id)
  with task dependencies from JSON files, or help users define task DAGs interactively.
  Use when user wants to create tasks, initialize workflows, sync dependencies to Consul,
  or set up new requirements for the agent workflow system. Triggers for phrases like:
  "create task", "sync to consul", "sync to harness", "new requirement", "add workflow",
  "initialize task", "sync dependencies", "create workflow".
allowed-tools:
  - Bash
  - Read
  - Write
---

# Harness Sync Skill

Sync workflow tasks to Consul KV for the Harness Framework.

## 前置检查：必填参数

在创建 workflow 或同步到 Consul 之前，**必须先确认以下参数**。如果缺失，**必须向用户提问并等待用户输入**，不得使用自动生成的值。

### 必填参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `req_id` | 需求唯一标识符 | `req-001`、`REQ-20260502-001` |
| `title` | 需求标题 | `"用户登录功能"` |

### 检查流程

1. 检查环境变量 `REQ_ID` 或用户是否已指定 `req_id`
2. **如果 `req_id` 缺失** → 提问用户：
   - "请提供 req_id（需求唯一标识符，如 `req-001` 或 `REQ-20260502-001`）："
3. **如果 `title` 缺失** → 提问用户：
   - "请提供需求标题（如 `用户登录功能`）："
4. 对于 `dependencies.json` 中的每个任务，确认 `agent_name` 和 `description` 是否已填写
5. **如果任务的 `agent_name` 为空** → 提问用户确认，不得自动生成

> **禁止行为**：不得自动生成 `req_id`、`title`、`agent_name`。每个值都必须由用户显式提供。`service_name` 仅是可选业务上下文。

## Consul 地址

默认 `127.0.0.1:8500`，可通过环境变量 `CONSUL_ADDR` 覆盖。

## 同步到 Consul（curl 版）

### 一、准备 dependencies.json

> **注意：** `agent_name` 和 `description` 是每个任务的**必填字段**。如果缺失，必须向用户确认。

```json
{
  "design-api": {
    "type": "design",
    "depends_on": [],
    "agent_name": "design-agent",
    "service_name": "platform",
    "description": "为登录功能设计 API 契约"
  },
  "review-design": {
    "type": "review",
    "depends_on": ["design-api"],
    "agent_name": "review-agent",
    "service_name": "platform",
    "description": "评审 API 设计"
  },
  "build-user-service": {
    "type": "backend",
    "depends_on": ["review-design"],
    "agent_name": "backend-agent",
    "service_name": "user-service",
    "description": "实现 user-service 的登录接口"
  },
  "test-e2e": {
    "type": "test",
    "depends_on": ["build-user-service"],
    "agent_name": "test-agent",
    "service_name": "platform",
    "description": "端到端登录流程测试"
  }
}
```

### 二、用 curl 同步到 Consul

```bash
CONSUL=http://127.0.0.1:8500
REQ_ID=req-001
TITLE="用户登录功能"
DEPS_FILE=/tmp/dependencies.json

# 1. 写入需求元数据
curl -s -X PUT "$CONSUL/v1/kv/workflows/$REQ_ID/title" -d "$TITLE"
curl -s -X PUT "$CONSUL/v1/kv/workflows/$REQ_ID/dependencies" -d "$(cat $DEPS_FILE)"
curl -s -X PUT "$CONSUL/v1/kv/workflows/$REQ_ID/created_at" -d "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# 2. 批量写入任务（遍历 JSON keys）
cat $DEPS_FILE | python3 -c "
import sys, json, subprocess
consul = 'http://127.0.0.1:8500'
deps = json.load(sys.stdin)
for task, info in deps.items():
    upstream = info.get('depends_on', [])
    status = 'PENDING' if not upstream else 'BLOCKED'
    base = f'workflows/$/REQ_ID/tasks/{task}'
    cmds = [
        f'curl -s -X PUT {consul}/v1/kv/{base}/status -d {status}',
        f'curl -s -X PUT {consul}/v1/kv/{base}/type -d {info.get(\"type\",\"generic\")}',
        f'curl -s -X PUT {consul}/v1/kv/{base}/agent_name -d {info.get(\"agent_name\",\"\")}',
        f'curl -s -X PUT {consul}/v1/kv/{base}/service_name -d {info.get(\"service_name\",\"\")}',
        f'curl -s -X PUT {consul}/v1/kv/{base}/description -d {info.get(\"description\",\"\")}',
        f'curl -s -X PUT {consul}/v1/kv/{base}/created_at -d $(date -u +%Y-%m-%dT%H:%M:%SZ)',
    ]
    if upstream:
        cmds.append(f'curl -s -X PUT {consul}/v1/kv/{base}/depends_on -d {\",\".join(upstream)}')
    for cmd in cmds:
        subprocess.run(cmd, shell=True)
"

# 3. 草稿模式：设为 false（默认），或发布：设为 true
curl -s -X PUT "$CONSUL/v1/kv/workflows/$REQ_ID/published" -d "false"
```

### 三、用 Python 脚本同步（备选）

```bash
python3 skills/harness-sync/scripts/sync_to_consul.py <req_id> <dependencies.json> [--title "需求标题"]
```

示例：
```bash
python3 skills/harness-sync/scripts/sync_to_consul.py req-001 /tmp/dependencies.json --title "用户登录功能"
```

## 任务类型

| Type | Description |
|------|-------------|
| `design` | API/架构设计任务 |
| `review` | 设计评审任务 |
| `backend` | 后端开发任务 |
| `test` | 测试任务 |
| `deploy` | 部署任务 |

## 任务状态流转

- **BLOCKED**: 有依赖项的任务，初始状态
- **PENDING**: 无依赖的叶子任务，或被 Aggregator 激活
- **IN_PROGRESS**: Agent 正在执行
- **DONE**: 任务完成
- **FAILED**: 任务失败
- **ABORTED**: 任务中止

## Consul KV 结构

同步后在 Consul 中创建以下 key：

```
workflows/<req_id>/
├── title                    # 需求标题
├── control                  # PAUSE | RESUME | ABORT
├── dependencies             # 任务依赖拓扑 JSON
├── published                # false（草稿模式，需发布后才激活）
├── created_at              # 创建时间
└── tasks/<task_name>/
    ├── status              # BLOCKED | PENDING | IN_PROGRESS | DONE | FAILED
    ├── type                # design | review | backend | test | deploy
    ├── agent_name          # 逻辑执行者名称（必填）
    ├── service_name        # 可选业务或仓库归属
    ├── description         # 任务描述（必填）
    └── created_at          # 创建时间
```

## 交互式创建依赖文件

如果没有现成的 JSON 文件，**必须通过以下交互式流程收集用户输入**，不得跳过任何步骤或自动生成值：

### 步骤 1：收集基本信息

向用户逐一提问：
1. "请提供 req_id（需求唯一标识符）：" — 如 `req-001`
2. "请提供需求标题：" — 如 `用户登录功能`

### 步骤 2：收集任务列表

对每个任务，向用户确认以下信息（不可自动填充）：
- `task_name` - 任务名称（唯一标识），如 `design-api`
- `type` - 任务类型：`design` | `review` | `backend` | `test` | `deploy`
- `depends_on` - 依赖任务列表（数组），如 `["design-api"]`
- `agent_name` - **逻辑执行者名称（必填）**，如 `backend-agent`
- `service_name` - 可选业务或仓库归属，如 `user-service`
- `description` - **任务描述（必填）**，如 `为登录功能设计 API 契约`

> **关键约束**：`agent_name` 和 `description` 不得为空，不得自动生成。如果用户未提供，必须追问。

### 步骤 3：生成并同步

收集完成后保存为 JSON，执行 sync 命令。

## 示例会话

```
User: create a new workflow for user registration
Assistant:
  1. What is the req_id? (e.g., req-002)
  2. What is the title? "用户注册功能"
  3. What tasks do you need? (list them)

  Let me create the dependencies.json:

  {
    "design-api": {"type": "design", "depends_on": [], "agent_name": "design-agent", "description": "..."},
    "build-backend": {"type": "backend", "depends_on": ["design-api"], "agent_name": "backend-agent", "service_name": "user-service", "description": "..."},
    ...
  }

  Ready to sync? Run:
  curl -s -X PUT "http://127.0.0.1:8500/v1/kv/workflows/req-002/title" -d "用户注册功能"
  curl -s -X PUT "http://127.0.0.1:8500/v1/kv/workflows/req-002/dependencies" -d @deps.json
  curl -s -X PUT "http://127.0.0.1:8500/v1/kv/workflows/req-002/published" -d "true"
```
