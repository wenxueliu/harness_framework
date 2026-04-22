# 任务间消息通信

框架提供基于 Consul KV 的异步消息队列，支持任务间的协作请求。

## 通信模型

```
Task A                              Task B
  │                                    │
  │  发现需要 B 配合                     │  轮询自己的队列
  │                                    │
  ▼                                    ▼
发送请求 ──────────────────────────► 队列 task-B
  │                                    │
  │                                    ▼
  │                              处理请求
  │                                    │
  │  轮询等待响应                        │
  ◄────────────────────────────────────┘
  │
  ▼
继续执行
```

## Consul KV 结构

```
workflows/<req_id>/requests/<task_name>/
└── <msg_id>                    # 消息内容 (JSON)
    {
      "msg_id": "msg-xxx",
      "from": "task-frontend",   # 发送方
      "to": "task-backend",      # 接收方
      "action": "provide_api",   # 动作类型
      "params": {...},           # 请求参数
      "status": "PENDING",       # PENDING | PROCESSING | DONE | FAILED | TIMEOUT
      "result": null,            # 处理结果
      "created_at": "...",
      "timeout": 300
    }
```

## 消息状态流转

```
PENDING ──► PROCESSING ──► DONE
    │            │
    │            ▼
    └──────► FAILED / TIMEOUT
```

## Agent 端脚本

| 脚本 | 说明 |
|------|------|
| `message_send.py` | 发送消息到目标任务 |
| `message_poll.py` | 轮询当前任务队列 |
| `message_complete.py` | 完成消息处理 |

### 发送消息

```bash
python scripts/message_send.py <req_id> <to_task> <action> [--params JSON] [--timeout SECONDS]

# 示例
python scripts/message_send.py req-001 task-backend provide_api --params '{"endpoint": "/api/user"}'
```

### 轮询消息

```bash
python scripts/message_poll.py <req_id> [--task TASK] [--status PENDING] [--limit N]

# 示例
python scripts/message_poll.py req-001 --status PENDING
```

### 完成消息

```bash
python scripts/message_complete.py <req_id> <msg_id> [--task TASK] --result JSON

# 示例
python scripts/message_complete.py req-001 msg-abc123 --result '{"endpoint": "/api/v1/user"}'
```

## WebAPI 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/workflow/<req_id>/messages/<task>` | 获取任务队列消息 |
| POST | `/api/workflow/<req_id>/messages` | 发送消息 |

### GET /api/workflow/req-001/messages/task-backend

返回指定任务队列中的消息列表。

```json
{
  "req_id": "req-001",
  "task": "task-backend",
  "messages": [
    {
      "msg_id": "msg-xxx",
      "from": "task-frontend",
      "to": "task-backend",
      "action": "provide_api",
      "params": {"endpoint": "/api/user"},
      "status": "PENDING",
      "result": null,
      "created_at": "2025-04-22T10:00:00Z",
      "timeout": 300
    }
  ]
}
```

### GET /api/workflow/req-001/messages/task-backend?status=PENDING

支持按状态过滤：`PENDING`、`PROCESSING`、`DONE`、`FAILED`、`TIMEOUT`

### POST /api/workflow/req-001/messages

发送消息到目标任务。

Request Body:
```json
{
  "from": "task-frontend",
  "to": "task-backend",
  "action": "provide_api",
  "params": {"endpoint": "/api/user"},
  "timeout": 600
}
```

Response:
```json
{
  "ok": true,
  "msg_id": "msg-xxx",
  "message": {...}
}
```

## 设计原则

1. **每个任务一个队列**：消息发送到目标任务的队列，避免冲突
2. **FIFO 排序**：按 `created_at` 排序，先到先处理
3. **Agent 自主决策**：何时轮询、是否等待、如何处理，由 Agent 自己决定
4. **可扩展优先级**：预留 `priority` 字段，后续可扩展

## 使用场景

### 场景一：设计服务需要后端配合

```
design 服务执行中，发现需要后端提供接口定义
    │
    ▼
design 发送消息给 backend：
  action: "provide_api_doc"
  params: {"service": "user"}
    │
    ▼
backend 的 Agent 轮询队列，收到请求
    │
    ▼
完成接口文档编写，写入 result
    │
    ▼
design 轮询检测到 DONE，获取 result
    │
    ▼
继续设计工作
```

### 场景二：后端需要前端配合

```
backend 服务执行中，发现前端需要确认接口格式
    │
    ▼
backend 发送消息给 frontend：
  action: "confirm_api_format"
  params: {"endpoint": "/api/user", "format": "REST"}
    │
    ▼
frontend 的 Agent 轮询队列，收到请求
    │
    ▼
确认格式，写入 result
    │
    ▼
backend 获取响应，继续开发
```
