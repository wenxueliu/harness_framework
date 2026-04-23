# 使用指南

## 快速开始

### 1. 启动 Consul

```bash
./scripts/start_consul_dev.sh
# 或
consul agent -dev -ui -bind=127.0.0.1
```

### 2. 启动框架

```bash
python -m harness_framework.daemon
# 访问 http://127.0.0.1:8080 查看 WebAPI
```

### 3. 初始化需求

```bash
python scripts/sync_to_consul.py req-001 dependencies.json \
  --title "用户登录功能" --publish
```

## 定义 dependencies.json

```json
{
  "design-api": {
    "type": "design",
    "depends_on": [],
    "service_name": "platform",
    "description": "设计登录 API 契约"
  },
  "review-design": {
    "type": "review",
    "depends_on": ["design-api"],
    "service_name": "platform",
    "description": "评审 API 设计"
  },
  "build-backend": {
    "type": "backend",
    "depends_on": ["review-design"],
    "service_name": "user-service",
    "description": "实现登录接口"
  },
  "test-e2e": {
    "type": "test",
    "depends_on": ["build-backend"],
    "service_name": "platform",
    "description": "端到端测试"
  }
}
```

**字段说明**：

| 字段 | 必填 | 说明 |
|------|------|------|
| `type` | 是 | 任务类型：design / review / backend / test / deploy |
| `depends_on` | 是 | 依赖任务列表（数组），无依赖则空数组 |
| `service_name` | 是 | 关联服务名，用于 Agent 过滤 |
| `description` | 是 | 任务描述 |

## Agent 工作流

### 启动 Agent

```bash
# 设置环境变量
export CONSUL_ADDR=127.0.0.1:8500
export AGENT_ID=my-agent
export SERVICE_NAME=user-service
export REPO_PATH=/path/to/your/service

# 启动心跳（后台）
python scripts/heartbeat.py --loop 10 &
```

### 抢占任务

```bash
# 抢占指定任务
python scripts/claim_task.py req-001 design-api

# 或自动抢占下一个可用任务
python scripts/claim_next_task.py --loop
```

### 执行任务

```bash
# 记录日志
python scripts/log_step.py req-001 "开始设计 API 契约"

# 读取上游上下文
python scripts/read_context.sh req-001

# 写产物
python scripts/write_artifact.sh req-001 pr_url "https://..."

# 完成任务
python scripts/complete_task.py req-001 design-api \
  --meta '{"branch":"feature/login","commit":"abc123"}'
```

### 任务失败

```bash
python scripts/fail_task.py req-001 build-backend \
  --error "数据库连接失败" \
  --retry-hint retry
```

## 测试 Agent 反馈流程

```bash
# 抢占测试任务
python scripts/claim_task.py req-001 test-e2e

# 测试失败，归因到具体服务
python scripts/feedback_write.py req-001 user-service \
  --error "登录接口返回 500" \
  --severity high

# 标记任务失败
python scripts/fail_task.py req-001 test-e2e \
  --error "2 个服务失败: user-service, order-service"
```

服务 Agent 接收反馈：

```bash
# 监听反馈（阻塞）
python scripts/feedback_listen.py req-001 user-service --timeout 600

# 完成修复
python scripts/feedback_resolve.py req-001 user-service \
  --summary "修复了 NPE 异常" \
  --commit "$(git rev-parse HEAD)"
```

## 动态任务提案（Proposal）

Agent 发现遗漏时：

```bash
# 提出新任务
# 1. 读 deps
DEPS=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/dependencies?raw")

# 2. 添加新任务（见 proposal-protocol.md）
# ...

# 3. CAS 设置 Proposal
INDEX=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status" | \
  python3 -c "import sys,json; print(json.load(sys.stdin)[0]['ModifyIndex'])")
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status?cas=$INDEX" -d "Proposal"

# 4. 正常结束当前任务
python scripts/complete_task.py req-001 $TASK_NAME
```

人工确认：

```bash
# 确认全部
INDEX=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status" | \
  python3 -c "import sys,json; print(json.load(sys.stdin)[0]['ModifyIndex'])")
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status?cas=$INDEX" -d "CONFIRMED"
```

## 人工干预

### 暂停/恢复流程

```bash
# 暂停
curl -X PUT "http://127.0.0.1:8500/v1/kv/workflows/req-001/control" -d "PAUSE"

# 恢复
curl -X DELETE "http://127.0.0.1:8500/v1/kv/workflows/req-001/control"
```

### 中止流程

```bash
curl -X PUT "http://127.0.0.1:8500/v1/kv/workflows/req-001/control" -d "ABORT"
```

### 重试失败任务

```bash
curl -X PUT "http://127.0.0.1:8500/v1/kv/workflows/req-001/tasks/build-backend/status" -d "PENDING"
```

## WebAPI

框架提供 HTTP API 用于查询和控制：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/workflows` | GET | 列出所有需求及进度 |
| `/api/workflow/<req_id>` | GET | 获取需求详情 |
| `/api/workflow/<req_id>/control` | POST | PAUSE / RESUME / ABORT |
| `/api/workflow/<req_id>/retry/<task>` | POST | 重试失败任务 |
| `/api/agents` | GET | 列出活跃 Agent |

## 查看状态

```bash
# 直接查 Consul
curl -s "http://127.0.0.1:8500/v1/kv/workflows/req-001/?recurse=true"

# 通过 WebAPI
curl -s "http://127.0.0.1:8080/api/workflows" | python3 -m json.tool
```

## 目录结构

```
harness_framework/
├── daemon.py          # 主进程入口
├── aggregator.py      # DAG 调度器
├── watchdog.py        # Agent 存活检测 + 超时恢复
├── webapi.py          # HTTP API
├── consul_client.py   # Consul 客户端
├── message_bus.py    # 任务间消息
└── workflow_skills.py # Agent Skill（Proposal 等）

scripts/
├── sync_to_consul.py   # 初始化需求
├── start_consul_dev.sh # 启动 Consul
└── ...

skills/stage-bridge/scripts/
├── heartbeat.py         # Agent 心跳
├── claim_task.py        # 抢占任务
├── complete_task.py     # 完成任务
├── feedback_listen.py   # 监听反馈
└── ...
```

## 常见问题

**Q: Agent 抢占任务失败？**
- 检查任务状态是否为 `PENDING`
- 检查 `assigned_agent_hint` 是否有其他 Agent 优先权

**Q: 心跳失败 404？**
- Agent 已被自动注销，需要重新注册

**Q: Aggregator 不调度任务？**
- 检查 `published` 是否为 `true`
- 检查 `control` 是否为 `PAUSE` 或 `ABORT`
- 检查需求状态是否为 `Proposal`

**Q: Watchdog 回滚任务？**
- 检查 Agent 是否存活
- 检查任务是否超时（默认 1 小时）

## 相关文档

- [status-state-machine.md](./status-state-machine.md) — 状态机定义
- [proposal-protocol.md](./proposal-protocol.md) — 动态提案协议
- [dynamic-tasks.md](./dynamic-tasks.md) — 动态任务设计
- [message-bus.md](./message-bus.md) — 消息通信
- [agent-retry-pattern.md](./agent-retry-pattern.md) — 重试模式
- [memory-model.md](./memory-model.md) — 记忆模型
