---
name: add-task
description: |
  Incrementally add a single task to an existing workflow in Consul.
  Use when a task was missed in the initial dependencies.json, or when an agent
  discovers work that needs to be done mid-execution and proposes a new task.
  Performs constraint validation: rejects if dependencies are FAILED/ABORTED,
  or if completed tasks depend on the new task.
  Triggers: "add task", "new task", "增量添加任务", "补充任务", "插入任务"
allowed-tools:
  - Bash
  - Read
---

# Add Task — 增量添加任务

## 前置检查：必填参数

在添加任务之前，**必须先确认以下参数**。如果缺失，**必须向用户提问并等待用户输入**，不得自动生成。

### 必填参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `req_id` | 目标 Workflow ID | `req-001` |
| `task_name` | 新任务名称（唯一） | `e2e-test` |
| `--description` | 任务描述 | `"端到端登录流程测试"` |
| `--type` | 任务类型 | `backend`、`test`、`design`、`review`、`deploy` |
| `--service-name` | 关联服务名 | `user-service` |

### 检查流程

1. **如果用户未提供 `req_id`** → 提问用户：
   - "请提供目标 Workflow 的 req_id（如 `req-001`）："
2. **如果用户未提供 `task_name`** → 提问用户：
   - "请提供新任务的名称（唯一标识）："
3. **如果用户未提供 `--description`** → 提问用户：
   - "请提供任务描述："
4. **如果用户未提供 `--type`** → 提问用户：
   - "请选择任务类型：design / review / backend / test / deploy"
5. **如果用户未提供 `--service-name`** → 提问用户：
   - "请提供此任务关联的服务名（如 `user-service`）："

> **禁止行为**：不得自动生成 `req_id`、`task_name`、`service_name`。每个值都必须由用户显式提供。

## 何时使用

当 workflow 已创建并开始执行后，发现缺少某个任务时需要增量添加。场景包括：

- 设计阶段发现遗漏了一个子任务
- Agent 在执行过程中发现需要额外的步骤
- 人为调整：在已有 workflow 中插入新任务

## 用法

```bash
python scripts/add_task.py <req_id> <task_name> \
  --description "任务描述" \
  --type backend \
  --depends-on design,review \
  --service-name user-service
```

### 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `req_id` | 是 | Workflow ID（如 `req-001`） |
| `task_name` | 是 | 新任务名称（唯一，不能与现有任务重名） |
| `--description` | 否 | 任务描述 |
| `--type` | 否 | 任务类型：`design` `review` `backend` `test` `deploy` `generic`（默认 `generic`） |
| `--depends-on` | 否 | 上游依赖，逗号分隔（如 `design,review`） |
| `--service-name` | 否 | 关联服务名 |
| `--consul` | 否 | Consul 地址（默认 `CONSUL_ADDR` 环境变量或 `127.0.0.1:8500`） |

## 约束检查

`add_task.py` 在写入前执行双向约束验证，防止破坏已有 workflow：

### 检查 1：上游依赖不能是 FAILED/ABORTED

```text
Error: Cannot add task. The following dependencies will never complete:
  design-api (FAILED), auth-service (ABORTED)
```

FAILED/ABORTED 的任务永远不会完成，新任务依赖它们会导致永久 BLOCKED。

### 检查 2：已有的已完成任务不能依赖新任务

```text
Error: Cannot add task. The following already-completed tasks depend on it:
  deploy (DONE), e2e-test (DONE)
```

已 DONE/FAILED/ABORTED 的任务不会重新执行，如果它们依赖新任务，流程将永远无法完成。

### 检查 3：任务不能重名

```text
Error: Task 'backend' already exists in workflow 'req-001'
```

### 初始状态自动推导

| 条件 | 初始状态 |
|------|---------|
| 无依赖 | `PENDING` |
| 有依赖且所有上游已完成（DONE） | `PENDING` |
| 有依赖且部分上游未完成 | `BLOCKED` |

## 示例

### 添加叶子任务

```bash
python scripts/add_task.py req-001 e2e-test \
  --description "端到端登录流程测试" \
  --type test \
  --depends-on deploy \
  --service-name platform
```

### 添加无依赖任务

```bash
python scripts/add_task.py req-001 init-db \
  --description "初始化数据库表结构" \
  --type backend \
  --service-name user-service
```

### 添加设计评审任务

```bash
python scripts/add_task.py req-001 review-design \
  --description "评审 API 设计方案" \
  --type review \
  --depends-on design-api \
  --service-name platform
```

## 典型使用场景

### 场景 1：设计 Agent 发现遗漏

设计 Agent 执行 `design` 任务时发现需要补充一个 `db-schema` 任务：

```bash
# 设计 Agent：读取 workflow 上下文后决定补充任务
python scripts/add_task.py req-001 db-schema \
  --description "设计数据库 schema" \
  --type design \
  --depends-on design-api \
  --service-name user-service

# 写入产物说明
python skills/stage-bridge/scripts/write_artifact.py req-001 extra-tasks \
  '[{"name":"db-schema","reason":"API 设计发现需要定义表结构"}]'
```

### 场景 2：人工介入调整

操作者发现 dependencies.json 遗漏了一个部署步骤：

```bash
python scripts/add_task.py req-001 deploy-canary \
  --description "金丝雀发布到预发环境" \
  --type deploy \
  --depends-on build --service-name platform
```

## 集成流程

在已有 workflow 中增量添加任务的典型流程：

```
1. Agent 或人发现需要新任务
       │
       ▼
2. 检查约束（add_task.py 自动做）
       │
       ▼
3. add_task.py 写入新任务到 Consul KV
       │
       ▼
4. 如果初始状态是 PENDING，Aggregator 会看到它并等待依赖满足
   如果初始状态是 BLOCKED，上下游任务完成后自动激活
       │
       ▼
5. 已有 Agent 或新 Agent 抢占并执行新任务
```

## 注意事项

- **仅支持 Consul 模式**：`add_task.py` 通过 HTTP 连接 Consul，不支持 `--local-file` 模式
- **建议在 workflow 发布前补全**：`published=true` 后添加任务是可行的，但可能影响正在执行的 Agent 的任务排序
- **与 file-kv skill 配合**：local-file 模式下需直接操作 JSON 文件（见 `skills/file-kv/SKILL.md`）
