# 5 分钟入门：定义你的第一个工作流

> **初次接触？** 先看 [quickstart.md](quickstart.md)。本文带你定义第一个有依赖关系的 DAG 工作流。

这篇文章带你定义一个 3 个任务、有依赖关系的 DAG 流水线，然后看框架如何自动推进。

## 1. 创建工作流描述文件

新建 `my-first-dag.json`：

```json
{
  "design": {
    "type": "design",
    "depends_on": [],
    "service_name": "myservice",
    "description": "设计 REST API 端点：GET /api/tasks、POST /api/tasks"
  },
  "backend": {
    "type": "backend",
    "depends_on": ["design"],
    "service_name": "myservice",
    "description": "实现 /api/tasks 端点的 CRUD 逻辑"
  },
  "test": {
    "type": "test",
    "depends_on": ["backend"],
    "service_name": "myservice",
    "description": "对 /api/tasks 做集成测试：CRUD 四条路径 + 错误场景"
  }
}
```

**依赖关系**：
```
design ──→ backend ──→ test
```

- `design` 无依赖，是最上游的叶子任务
- `backend` 依赖 `design` 完成
- `test` 依赖 `backend` 完成

## 2. 启动框架

```bash
python -m harness_framework.daemon --local
```

保持终端开着。

## 3. 初始化工作流

```bash
python scripts/sync_to_consul.py my-first-dag.json \
  --req-id myapp-001 --title "我的第一个工作流" --publish
```

加了 `--publish`，工作流会被 Aggregator 和 Watchdog 接管。

## 4. 观察 DAG 自动推进

```bash
# 查看整体状态
curl -s http://127.0.0.1:8080/api/workflows | python3 -m json.tool
```

你会看到：

```
myapp-001 → phase=RUNNING, progress=0%
├── design    → PENDING    ← Aggregator 检测到无依赖，已激活
├── backend   → BLOCKED    ← 等待 design 完成
└── test      → BLOCKED    ← 等待 backend 完成
```

**关键点**：`design` 自动变成了 `PENDING` —— Aggregator 检测到它没有依赖，直接激活了。`backend` 和 `test` 还是 `BLOCKED`，因为它们的上游还没完成。

## 5. 模拟 Agent 执行（手动）

在单机模式下，Agent 就是你自己。你可以手动模拟 Agent 的行为：

```bash
# Agent 认领 design 任务
curl -s -X PUT \
  "http://127.0.0.1:8500/v1/kv/workflows/myapp-001/tasks/design/status" \
  -d "IN_PROGRESS"

curl -s -X PUT \
  "http://127.0.0.1:8500/v1/kv/workflows/myapp-001/tasks/design/assigned_agent" \
  -d "my-agent"

# Agent 完成 design 任务
curl -s -X PUT \
  "http://127.0.0.1:8500/v1/kv/workflows/myapp-001/tasks/design/status" \
  -d "DONE"
```

等几秒（Aggregator 默认 5s 轮询），再查看：

```bash
curl -s http://127.0.0.1:8080/api/workflows | python3 -m json.tool
```

现在 `backend` 变成了 `PENDING`——因为 `design` 已完成，Aggregator 自动激活了下游。

```
myapp-001 → phase=RUNNING, progress=33%
├── design    → DONE
├── backend   → PENDING    ← 被 Aggregator 激活
└── test      → BLOCKED
```

## 6. 同理完成剩余任务

```bash
# backend → DONE
curl -s -X PUT http://127.0.0.1:8500/v1/kv/workflows/myapp-001/tasks/backend/status -d "IN_PROGRESS"
curl -s -X PUT http://127.0.0.1:8500/v1/kv/workflows/myapp-001/tasks/backend/assigned_agent -d "my-agent"
curl -s -X PUT http://127.0.0.1:8500/v1/kv/workflows/myapp-001/tasks/backend/status -d "DONE"

# test → DONE
curl -s -X PUT http://127.0.0.1:8500/v1/kv/workflows/myapp-001/tasks/test/status -d "IN_PROGRESS"
curl -s -X PUT http://127.0.0.1:8500/v1/kv/workflows/myapp-001/tasks/test/assigned_agent -d "my-agent"
curl -s -X PUT http://127.0.0.1:8500/v1/kv/workflows/myapp-001/tasks/test/status -d "DONE"
```

再次查看——所有任务 DONE，流程结束。

```
myapp-001 → phase=DONE, progress=100%
```

## 理解：Agent 在实际中怎么工作

上面那些 curl 命令，在实际使用中由 Agent（Claude Code、OpenCode 等）自动执行：

1. **认领任务**：Agent 轮询 `PENDING` 任务，CAS 抢占
2. **设为 IN_PROGRESS**：抢占成功后标记正在执行
3. **完成**：执行完毕标记 `DONE`
4. **框架推进**：Aggregator 检测到上游 DONE → 激活下游

Agent 不需要知道整个 DAG——它只需要知道"有什么 PENDING 任务我可以做"。

## 下一步

| 我想… | 看这里 |
|-------|--------|
| 理解任务状态机（BLOCKED → PENDING → IN_PROGRESS → DONE/FAILED） | [核心概念 →](concepts.md) |
| 把真实 Agent 接入框架（认证、心跳、抢占） | [Agent 接入指南 →](agent-guide.md) |
| 了解故障恢复（超时、Agent 死亡如何处理） | [核心概念 →](concepts.md) |
| 查看所有可用的 WebAPI 端点 | [操作手册 →](usage-guide.md) |
