---
name: harness-sync
description: |
  Sync workflow tasks to Consul KV for the Harness Framework. Create new requirements (req_id)
  with task dependencies from JSON files, or help users define task DAGs interactively.
  Use when user wants to create tasks, initialize workflows, sync dependencies to Consul,
  or set up new requirements for the agent workflow system. Triggers for phrases like:
  "create task", "sync to consul", "new requirement", "add workflow", "initialize task",
  "sync dependencies", or when working with workflows/req_id in Harness Framework.
triggers:
  - create task
  - sync to consul
  - sync to harness
  - new requirement
  - add workflow
  - initialize task
  - sync dependencies
  - create workflow
  - harness framework task
allowed-tools:
  - Bash
  - Read
  - Write
---

# Harness Sync Skill

Sync workflow tasks to Consul KV for the Harness Framework.

## Prerequisites

- Consul must be running at `127.0.0.1:8500` (or set `CONSUL_ADDR` env var)
- Python 3 with the harness_framework module available

## Core Script: `sync_to_consul.py`

The main script is at `scripts/sync_to_consul.py` (relative to the project root). It reads a dependencies JSON file and syncs it to Consul.

### Usage

```bash
python3 scripts/sync_to_consul.py <req_id> <dependencies.json> [--title "需求标题"]
```

Or from the Harness Framework project directory:
```bash
python3 scripts/sync_to_consul.py <req_id> examples/dependencies.example.json --title "需求标题"
```

### Example dependencies.json Format

```json
{
  "design-api": {
    "type": "design",
    "depends_on": [],
    "service_name": null,
    "description": "为登录功能设计 API 契约"
  },
  "review-design": {
    "type": "review",
    "depends_on": ["design-api"],
    "service_name": null,
    "description": "评审 API 设计"
  },
  "build-user-service": {
    "type": "backend",
    "depends_on": ["review-design"],
    "service_name": "user-service",
    "description": "实现 user-service 的登录接口"
  },
  "test-e2e": {
    "type": "test",
    "depends_on": ["build-user-service"],
    "service_name": null,
    "description": "端到端登录流程测试"
  }
}
```

### Task Types

| Type | Description |
|------|-------------|
| `design` | API/架构设计任务 |
| `review` | 设计评审任务 |
| `backend` | 后端开发任务 |
| `test` | 测试任务 |
| `deploy` | 部署任务 |

### Task Status Flow

- **BLOCKED**: 有依赖项的任务，初始状态
- **PENDING**: 无依赖的叶子任务，或被 Aggregator 激活
- **IN_PROGRESS**: Agent 正在执行
- **DONE**: 任务完成
- **FAILED**: 任务失败
- **ABORTED**: 任务中止

## Workflow

### Step 1: Check Consul Status

Verify Consul is running:
```bash
curl -s http://127.0.0.1:8500/v1/status/leader
```

### Step 2: Create or Use dependencies.json

**Option A: Use existing file**
```bash
python scripts/sync_to_consul.py req-001 examples/dependencies.example.json --title "用户登录功能"
```

**Option B: Create new file interactively**

Help user define the workflow by asking for:
1. `req_id` - 需求唯一标识符
2. `title` - 需求标题
3. Tasks with:
   - `task_name` - 任务名称（唯一标识）
   - `type` - 任务类型 (design/review/backend/test/deploy)
   - `depends_on` - 依赖任务列表（数组）
   - `service_name` - 关联服务名（可选）
   - `description` - 任务描述

### Step 3: Sync to Consul

Run the sync command:
```bash
python scripts/sync_to_consul.py <req_id> <deps_file> [--title "标题"]
```

### Step 4: Verify

Check the synced workflow:
```bash
curl -s http://127.0.0.1:8080/api/workflow/<req_id>
```

## Consul KV Structure

After syncing, the following keys are created:

```
workflows/<req_id>/
├── title                    # 需求标题
├── control                  # PAUSE | RESUME | ABORT
├── dependencies            # 任务依赖拓扑 JSON
├── created_at              # 创建时间
└── tasks/<task_name>/
    ├── status              # BLOCKED | PENDING | IN_PROGRESS | DONE | FAILED
    ├── type                # design | review | backend | test | deploy
    ├── service_name        # 关联服务（可选）
    ├── description         # 任务描述
    └── created_at          # 创建时间
```

## Interactive Mode

If user doesn't have a JSON file, help them create one:

1. Ask for the requirement ID (req_id)
2. Ask for the requirement title
3. For each task, ask:
   - Task name
   - Task type (select from list)
   - Dependencies (which tasks must complete first?)
   - Service name (optional)
   - Description

After collecting all tasks, save as JSON and run sync.

## Example Session

```
User: create a new workflow for user registration
Assistant:
  1. What is the req_id? (e.g., req-002)
  2. What is the title? "用户注册功能"
  3. What tasks do you need? (list them)

  Let me create the dependencies.json:

  {
    "design-api": {"type": "design", "depends_on": [], ...},
    "build-backend": {"type": "backend", "depends_on": ["design-api"], ...},
    ...
  }

  Ready to sync? Run:
  python scripts/sync_to_consul.py req-002 /tmp/deps.json --title "用户注册功能"
```
