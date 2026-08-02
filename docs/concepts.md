# 核心概念

> **初次接触？** 先看 [quickstart.md](quickstart.md) 和 [getting-started.md](getting-started.md)。本文是核心概念的完整说明。

## 整体架构

```
┌──────────────────────────────────────────────┐
│              框架主进程 (daemon.py)            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │Aggregator│  │ Watchdog │  │  WebAPI  │   │
│  │ DAG 推进 │  │ 故障恢复 │  │ HTTP API │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘   │
│       └──────────────┼─────────────┘         │
│                      │                        │
│             ┌────────▼────────┐              │
│             │  KV 存储接口     │              │
│             └────────┬────────┘              │
└──────────────────────┼──────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   ┌─────────┐  ┌───────────┐  ┌──────────┐
   │ Consul  │  │LocalStore │  │FileStore │
   │(HTTP)   │  │内存+HTTP  │  │JSON 文件 │
   └─────────┘  └───────────┘  └──────────┘
```

三个组件和存储层解耦——换存储后端只需一个命令行参数。

## 任务状态机

一个任务从创建到结束，经历这些状态：

```
┌──────────┐              ┌───────────┐
│ BLOCKED  │──────────────│  PENDING  │ ← Aggregator 激活（依赖全部 DONE）
└──────────┘              └─────┬─────┘
                                │ Agent 认领
                                ▼
                         ┌─────────────┐
                         │ IN_PROGRESS │
                         └──────┬──────┘
                                │
                   ┌────────────┼────────────┐
                   ▼            ▼            ▼
              ┌──────┐    ┌────────┐   ┌────────┐
              │ DONE │    │ FAILED │   │ABORTED │
              └──────┘    └────────┘   └────────┘
```

**BLOCKED**：初始状态，有未完成的上游依赖。
**PENDING**：所有依赖已满足，等待 Agent 认领。
**IN_PROGRESS**：Agent 正在执行。
**DONE**：成功完成。
**FAILED**：失败（可能是执行错误，也可能超时/Agent 崩溃超过重试上限）。

> 完整定义见 [status-state-machine.md](status-state-machine.md)。

## 三大组件

### Aggregator — DAG 推进引擎

Aggregator 只做一件事：**检测某个任务的所有上游依赖是否都是 DONE，如果是，就把该任务设为 PENDING**。

- 每 5 秒（可配置）轮询一次
- 不分配 Agent，不做 LLM 调用
- 纯规则引擎，行为完全可预测

### Watchdog — 僵尸任务回收

Watchdog 监控所有 IN_PROGRESS 的任务：

1. **Agent 死亡检测**：检查任务绑定的 Agent 是否还在发送心跳；Agent 死亡则回滚任务为 PENDING
2. **超时检测**：任务执行时间超过 `task_timeout`（默认 120 秒）则回滚

每次回滚 `retry_count` +1。超过 `max_retry`（默认 3）则标记 FAILED，写入告警。

> 单机模式下（`--local-file` 自动启用、`--standalone` 显式启用），默认 Agent ID 始终视为存活，不检查心跳。

### WebAPI — HTTP 服务

提供 REST API 供看板和脚本查询/控制：

| 端点 | 说明 |
|------|------|
| `GET /api/workflows` | 列出所有工作流及进度 |
| `GET /api/workflow/<req_id>` | 获取工作流详情 |
| `POST /api/workflow/<req_id>/control` | PAUSE / RESUME / ABORT |
| `GET /api/agents` | 列出活跃 Agent |
| `GET /api/health` | 健康检查 |

## 三种存储后端

| | Consul | Local（`--local`） | 纯文件（`--local-file`） |
|---|---|---|---|
| 启动方式 | `python -m harness_framework.daemon` | `--local` | `--local-file` |
| 外部依赖 | Consul 服务 | 无 | 无 |
| Agent 通信 | HTTP → Consul | HTTP → 内嵌服务器 | `file_kv.py` CLI |
| 适用场景 | 生产、多机 | 开发/测试、单机 | 单机、零网络 |
| 持久化 | Consul Raft | 可选 JSON 文件 | JSON 文件 |

三种模式都实现统一的 KVStore 接口，框架代码零改动即可切换。

> 详见 [storage-modes.md](storage-modes.md)。

## Agent 如何协作

### 不推不拉——Agent 主动认领

框架**不分配任务给 Agent**。Agent 自己轮询 PENDING 任务，通过 CAS（Check-And-Set）原子操作抢占：

```
Agent 轮询 → 发现 PENDING 任务 → CAS 抢占 → 成功则开始执行
                                        → 失败则继续找下一个
```

CAS 保证同一时刻只有一个 Agent 能抢到任务，无需分布式锁。

### Agent 与框架完全异步

Agent 只读写共享状态（KV 存储），不向框架发 RPC：

```
Agent                  KV 存储                框架
  │                      │                     │
  ├── 写 IN_PROGRESS ──→│                     │
  │                      │←── 轮询读取 ────────┤
  ├── 写 DONE ─────────→│                     │
  │                      │←── 检测到 DONE ────┤ → 激活下游
```

### 单机模式下的简化

单机模式（`--local-file` 自动启用、`--standalone` 显式启用）下：

- Agent 无需注册、无需心跳、无需注销
- 使用默认 Agent ID `standalone-agent`（可通过 `--standalone-agent-id` 自定义）
- Agent 直接认领任务、执行、标记完成

## 工作流数据模型

```
workflows/<req_id>/
├── published          # 是否发布（只有 published=true 才被处理）
├── title              # 需求标题
├── control            # 控制信号：PAUSE / RESUME / ABORT
├── dependencies       # 任务依赖拓扑（JSON）
├── versions/          # Requirement / WorkflowSpec / DAG / Plan 独立版本
├── changesets/        # 需求变更生命周期与审计历史
├── runs/              # 当前与历史 run；旧 run 可标记 SUPERSEDED
├── tasks/<task_name>/
│   ├── status         # 当前状态
│   ├── type           # design / review / backend / test / deploy
│   ├── assigned_agent # 正在执行的 Agent ID
│   ├── attempt_id / lease_epoch / lease_expires_at
│   ├── agent_contract # 输入/输出/职责/排除项/权限/上下文预算（JSON）
│   ├── context_inputs # 本任务允许注入的显式 selector
│   ├── artifacts/     # 版本化产物、manifest、checksum 与 lineage
│   ├── checkpoints/   # 长任务恢复点
│   ├── failure/       # 当前及历史 Failure Envelope
│   ├── evaluator/     # 评分迭代、fallback 和升级状态
│   ├── budget/        # 用量账本与 circuit breaker
│   ├── side_effects/  # 幂等副作用执行记录
│   └── retry_count    # 重试次数
├── knowledge/
│   ├── facts/         # 不可变事实
│   ├── artifacts/     # 跨任务版本化产物
│   ├── working_memory/# 按任务隔离的工作记忆
│   ├── events/        # 追加式事件
│   ├── summaries/     # 有界派生摘要
│   └── restricted/    # 默认拒绝访问的受限数据
└── human_interventions/ # evaluator/recovery 人工升级记录
```

旧 `context/` 路径仅作为迁移期兼容读取。新任务没有声明
`context_inputs` 时注入空上下文，不再隐式获得整个 workflow context。

## 生产执行契约

- **领取契约**：领取生成不可变 `attempt_id` 和单调 `lease_epoch`；所有产物、证据、checkpoint、用量与副作用写入都校验当前所有权。
- **完成契约**：required artifacts、verifier gates 与 circuit breaker 必须满足，任务才能进入 `DONE`。
- **验证契约**：`evaluator_policy` 限制每种策略迭代次数，检测评分平台期，并按 fallback chain 切换或升级人工。
- **恢复契约**：Failure Envelope 使用统一失败分类；`recovery_policy` 按 primary、narrowed、degraded、human 顺序选路。
- **变更契约**：ChangeSet 必须经过 impact analysis 和 approval；只失效下游闭包，旧 run 保留为 `SUPERSEDED`。

## 下一步

| 我想… | 看这里 |
|-------|--------|
| 查看完整的架构设计文档 | [架构设计 →](architecture.md) |
| 把所有配置项搞清楚 | [配置参考 →](configuration.md) |
| 接入真实 Agent 到框架 | [Agent 接入指南 →](agent-guide.md) |
| 了解动态任务提案 | [提案协议 →](proposal-protocol.md) |
| 了解任务间消息通信 | [消息总线 →](message-bus.md) |
| 配置任务内独立 Review 与人工确认 | [Executor–Reviewer 闭环 →](internal-review-loop.md) |
