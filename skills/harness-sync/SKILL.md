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

## Consul 地址

默认 `127.0.0.1:8500`，可通过环境变量 `CONSUL_ADDR` 覆盖。

## 同步到 Consul（curl 版）

### 一、准备 dependencies.json

> **注意：** `service_name` 和 `description` 是每个任务的**必填字段**。

```json
{
  "design-api": {
    "type": "design",
    "depends_on": [],
    "service_name": "platform",
    "description": "为登录功能设计 API 契约"
  },
  "review-design": {
    "type": "review",
    "depends_on": ["design-api"],
    "service_name": "platform",
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
    ├── service_name        # 关联服务（必填）
    ├── description         # 任务描述（必填）
    └── created_at          # 创建时间
```

## 交互式创建依赖文件

如果没有现成的 JSON 文件，按以下步骤帮助用户定义：

1. `req_id` - 需求唯一标识符
2. `title` - 需求标题
3. 每个任务：
   - `task_name` - 任务名称（唯一标识）
   - `type` - 任务类型 (design/review/backend/test/deploy)
   - `depends_on` - 依赖任务列表（数组）
   - `service_name` - 关联服务名（必填）
   - `description` - 任务描述（必填）

收集完成后保存为 JSON，然后执行 sync 命令。

## 示例会话

```
User: create a new workflow for user registration
Assistant:
  1. What is the req_id? (e.g., req-002)
  2. What is the title? "用户注册功能"
  3. What tasks do you need? (list them)

  Let me create the dependencies.json:

  {
    "design-api": {"type": "design", "depends_on": [], "service_name": "platform", "description": "..."},
    "build-backend": {"type": "backend", "depends_on": ["design-api"], "service_name": "user-service", "description": "..."},
    ...
  }

  Ready to sync? Run:
  curl -s -X PUT "http://127.0.0.1:8500/v1/kv/workflows/req-002/title" -d "用户注册功能"
  curl -s -X PUT "http://127.0.0.1:8500/v1/kv/workflows/req-002/dependencies" -d @deps.json
  curl -s -X PUT "http://127.0.0.1:8500/v1/kv/workflows/req-002/published" -d "true"
```
