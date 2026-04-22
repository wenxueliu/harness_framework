# Harness Framework

[English](#english) | [中文](#中文)

---

## English

### What is Harness Framework?

Harness Framework is the **framework layer** of a multi-Agent software development platform, running as a single Python process with threaded concurrency. All collaborative state is stored in **Consul KV**.

### Who is it for?

- **Engineering teams** running multi-Agent development workflows
- **Platform engineers** building AI-augmented software delivery pipelines
- **Organizations** adopting AI coding agents that need orchestration and coordination

### What problem does it solve?

When multiple AI agents collaborate on software development tasks, challenges emerge:

- **Task dependencies**: Downstream tasks need to wait for upstream completion
- **Agent failures**: Agents can crash or hang, leaving tasks stuck
- **Feedback loops**: Test failures need re-testing after fixes
- **Visibility**: Teams need a dashboard to monitor progress and intervene

Harness Framework solves these by providing:
- DAG-based task orchestration with automatic dependency resolution
- Automatic recovery from agent crashes and timeouts
- Intelligent re-test triggering when feedback is fixed
- REST API and dashboard integration for monitoring and control

### Core Value

**异构多 Agent 编排引擎——声明式定义工作流，可视化追踪执行，端到端韧性交付**

### Architecture

```
harness_framework/
├── daemon.py          # Main entry point: Aggregator + Watchdog + WebAPI threads
├── aggregator.py      # Listens to DAG changes, activates downstream tasks
├── watchdog.py        # Detects agent liveness and task timeout, auto-recovers
├── webapi.py          # HTTP API for dashboard queries and control signals
└── consul_client.py   # Consul HTTP client (stdlib only, no external deps)
```

**Three Core Components:**

| Component | Responsibility |
|-----------|----------------|
| **Aggregator** | Monitors DAG status; activates downstream tasks when dependencies are met; triggers re-test when all feedback is FIXED |
| **Watchdog** | Detects if agents are alive; detects task timeout (default 1h); rolls back to PENDING on failure (max 5 retries) |
| **WebAPI** | Provides aggregated queries and control signal write endpoints for business dashboards (with CORS) |

The execution layer (Agent processes) interacts with the framework via the `stage-bridge` Skill. All collaborative state is stored in **Consul KV**.

**Each task can be assigned an independent Agent**, enabling parallel execution across multiple agents simultaneously.

#### Harness Engineer Architecture

Harness Engineer is composed of two layers:

| Layer | Role | Description |
|-------|------|-------------|
| **Framework Layer** | Control & Constraint | Enforces workflow process, task orchestration, dependency resolution, auto-recovery, state management |
| **Capability Layer** | Execution | Heterogeneous AI coding agents that perform actual development work |

This design makes the platform **agent-agnostic**. Any Agent that implements the `stage-bridge` protocol can join the platform, regardless of its vendor or implementation:

- **Claude Code** — Anthropic's AI coding assistant
- **OpenCode** — Open source coding agent
- **Copilot / Codex** — GitHub's AI pair programmer
- **Custom Agents** — Any agent adhering to the stage-bridge protocol

The framework layer enforces **what must happen** (dependencies, retry logic, timeouts), while the capability layer focuses on **how it gets done** (code generation, testing, debugging).

### Quick Start

#### 1. Install Consul

See [Consul Official Installation Guide](https://developer.hashicorp.com/consul/install) for all platforms.

```bash
# macOS
brew install consul

# Linux
curl -LO https://releases.hashicorp.com/consul/1.18.1/consul_1.18.1_linux_amd64.zip
unzip consul_*.zip && sudo mv consul /usr/local/bin/

# Windows
# Download from https://developer.hashicorp.com/consul/install and add to PATH
```

> Note: This framework itself has **zero external Python dependencies** (stdlib only).

#### 2. Start all services

Open Claude Code and run `Run on Claude Code` to start all services (Consul dev mode, framework daemon, dashboard).

Alternatively, start services manually:

```bash
# Start Consul dev mode
./scripts/start_consul_dev.sh

# Start the framework daemon (default port 8080)
python -m harness_framework.daemon

# Initialize a workflow
python scripts/sync_to_consul.py req-001 examples/dependencies.example.json \
  --title "User Login Feature"
```

Visit [http://127.0.0.1:8500/ui](http://127.0.0.1:8500/ui) to view the Consul UI.

#### How to Define Tasks

Tasks are defined in `dependencies.json` and synced to Consul via `sync_to_consul.py`. Each task's `description` field tells the Agent **what to do**:

```json
{
  "backend": {
    "type": "backend",
    "depends_on": ["design"],
    "description": "Implement user login API: register / login / logout. Reference: docs/login-api.md"
  },
  "test": {
    "type": "test",
    "depends_on": ["backend"],
    "description": "Integration test for /api/login: happy path, wrong password, account not found. Tests at: tests/test_login.py"
  }
}
```

**Best practices for `description`**:
- State the goal directly: what to implement, test, or deploy
- Reference specific files or documents when relevant
- Keep it concise but actionable — the Agent will read this and execute autonomously

#### Incremental Task Addition

During execution, if the plan diverges from expectations, you can add new tasks to an existing workflow without re-initializing everything. Completed tasks remain untouched.

```bash
# Add a new task to an existing workflow
python scripts/add_task.py req-001 api-gateway \
  --description "Implement API gateway for authentication" \
  --type backend \
  --depends-on backend
```

**Constraints** (both directions are checked):
1. The new task's `depends_on` must not point to any task in `FAILED` or `ABORTED` state — those will never complete
2. No existing task that is already in a terminal state (`DONE` / `FAILED` / `ABORTED`) may depend on the new task — completed tasks won't re-run

If all dependencies are `DONE`, the new task becomes `PENDING` immediately. If dependencies are still running, it starts as `BLOCKED`.

#### Execution Flow

The framework runs these steps automatically:

| Step | Trigger | Action |
|------|---------|--------|
| **Task Activation** | All dependencies become DONE | Aggregator sets downstream task to PENDING |
| **Task Execution** | Agent picks up PENDING task | Agent writes IN_PROGRESS → DONE |
| **Fault Recovery** | Agent dies or task times out | Watchdog rolls back to PENDING (≤5 retries) |
| **Quality Gate** | Test fails | Wait for all feedback.FIXED → auto re-test (≤3 retries) |
| **Flow Termination** | All tasks DONE | Workflow complete; retry limit exceeded → FAILED |

### Usage Steps

1. **Define dependencies**: Write `dependencies.json` describing task topology
2. **Start Consul**: `./scripts/start_consul_dev.sh`
3. **Start framework**: `python -m harness_framework.daemon`
4. **Initialize requirement**: `python scripts/sync_to_consul.py <req_id> dependencies.json --title "Title"`
5. **Monitor status**: Access WebAPI or Consul UI
6. **Manual intervention**: Modify task status or reassign via API

### Enterprise-Level Complex Applications

The framework is designed to handle large-scale enterprise workflows with high parallelism and resilience.

**Horizontal Scaling**
- Run multiple framework instances (daemon processes) with different ports, each connecting to the same Consul cluster
- No single-point bottleneck — each instance operates independently on different workflows
- Task-level parallelism: multiple tasks across the DAG can execute simultaneously

**Multi-Workflow Orchestration**
- Support multiple concurrent requirements (`req-001`, `req-002`, ...) in the same Consul cluster
- Each requirement has its own DAG, isolated by prefix `workflows/<req_id>/`
- Aggregator automatically discovers and manages all workflows

**Resilience & Fault Tolerance**
- Watchdog detects agent crashes and task timeouts independently per task
- Automatic rollback to PENDING with retry count tracking (max 5 retries)
- Aggregator handles re-test logic when all feedback is fixed (max 3 retries)
- Consul's Raft consensus ensures state durability across node failures

**Operational Confidence**
- All state in Consul KV — easy to inspect, replay, and debug
- Control signals (PAUSE/RESUME/ABORT) allow manual intervention at any time
- REST API exposes aggregated view for dashboards without querying raw KV

**Deployment Topologies**

| Scenario | Consul | Framework Instances |
|----------|--------|---------------------|
| Development | Single node dev mode | 1 instance |
| Staging | 3-node cluster | 1-2 instances |
| Production | 5-node cluster + ACL | Multiple instances, distributed agents |

### Configuration

| Parameter | Default | Description |
|-----------|--------|-------------|
| `--consul` | `127.0.0.1:8500` | Consul HTTP address |
| `--token` | (empty) | Consul ACL Token (not needed for dev mode) |
| `--port` | `8080` | WebAPI listening port |
| `--aggregator-interval` | `5` | DAG polling interval (seconds) |
| `--watchdog-interval` | `30` | Zombie task detection interval (seconds) |
| `--task-timeout` | `3600` | Task timeout (seconds) |

Environment variables: `CONSUL_ADDR`, `CONSUL_TOKEN`

### Consul UI vs Business Dashboard

The Consul built-in UI is for **operations** — viewing raw KV content, service registrations, health checks.

The Harness business dashboard is for **developers and product** — DAG topology visualization, task progress, manual intervention.

They complement each other.

### Production Readiness

When upgrading from dev mode to a production 3-node Consul cluster, no code changes are needed. Just point `--consul` to the cluster address and configure the ACL Token.

---

## 中文

### 什么是 Harness Framework？

Harness Framework 是一个多 Agent 软件开发平台的**框架层**实现，以单一 Python 进程运行，内部通过线程并发协作。所有协作状态存储在 **Consul KV** 中。

### 谁适合使用？

- 正在运行多 Agent 开发工作流的工程团队
- 构建 AI 增强软件交付流水线的平台工程师
- 引入 AI 编程 Agent 并需要编排协调能力的组织

### 解决了什么问题？

当多个 AI Agent 协作完成软件开发任务时，会面临以下挑战：

- **任务依赖**：下游任务需要等待上游完成
- **Agent 故障**：Agent 可能崩溃或卡住，导致任务停滞
- **反馈闭环**：测试失败后需要修复再重测
- **可观测性**：团队需要看板来监控进度和人工干预

Harness Framework 提供以下能力：
- 基于 DAG 的任务编排，自动解析依赖关系
- Agent 崩溃或超时时自动恢复任务
- 当反馈修复后智能触发重测
- REST API 和看板集成，支持状态查询和人工干预

### 核心价值

**异构多 Agent 编排引擎——声明式定义工作流，可视化追踪执行，端到端韧性交付**

### 架构

```
harness_framework/
├── daemon.py          # 主入口：启动 Aggregator + Watchdog + WebAPI 线程
├── aggregator.py      # 监听 DAG 状态变更，激活下游任务
├── watchdog.py        # 检测 Agent 存活和任务超时，自动恢复
├── webapi.py          # HTTP API：看板聚合查询和控制信号写入
└── consul_client.py   # Consul HTTP 客户端（仅标准库）
```

**三大核心组件：**

| 组件 | 职责 |
|------|------|
| **Aggregator** | 轮询 DAG 状态，依赖全部完成时激活下游任务，所有 feedback 修复后触发重测 |
| **Watchdog** | 检测 Agent 是否存活，检测任务超时（默认 1h），超时或宕机时回滚为 PENDING（最多 5 次重试） |
| **WebAPI** | 为业务看板提供聚合查询与控制信号写入（含 CORS） |

执行层（Agent 进程）通过 `stage-bridge` Skill 与框架交互，所有协作状态存储在 **Consul KV** 中。

**每个任务都可以分配独立的 Agent**，支持多个 Agent 并行执行。

#### Harness Engineer 架构

Harness Engineer 由两层组成：

| 层级 | 角色 | 说明 |
|------|------|------|
| **框架层（Framework Layer）** | 控制与约束 | 强制执行工作流流程、任务编排、依赖解析、自动恢复、状态管理 |
| **能力层（Capability Layer）** | 执行能力 | 执行实际开发工作的异构 AI 编程 Agent |

这种设计使平台**与 Agent 类型无关**。任何实现了 `stage-bridge` 协议的 Agent 都可以加入平台，不受厂商或实现方式限制：

- **Claude Code** — Anthropic 的 AI 编程助手
- **OpenCode** — 开源编程 Agent
- **Copilot / Codex** — GitHub 的 AI 结对编程工具
- **自定义 Agent** — 任何遵循 stage-bridge 协议的 Agent

框架层负责约束**必须发生什么**（依赖、重试逻辑、超时），能力层负责实现**如何完成**（代码生成、测试、调试）。

### 快速开始

#### 1. 安装 Consul

参见 [Consul 官方安装指南](https://developer.hashicorp.com/consul/install) 获取各平台安装方式。

```bash
# macOS
brew install consul

# Linux
curl -LO https://releases.hashicorp.com/consul/1.18.1/consul_1.18.1_linux_amd64.zip
unzip consul_*.zip && sudo mv consul /usr/local/bin/

# Windows
# 从 https://developer.hashicorp.com/consul/install 下载并添加到 PATH
```

> 注意：框架本身**零外部 Python 依赖**（仅标准库）。

#### 2. 启动所有服务

打开 Claude Code，运行 `Run on Claude Code` 即可启动所有服务（Consul dev mode、框架守护进程、看板）。

手动启动方式：

```bash
# 启动 Consul dev mode
./scripts/start_consul_dev.sh

# 启动框架主进程（默认端口 8080）
python -m harness_framework.daemon

# 初始化一个工作流
python scripts/sync_to_consul.py req-001 examples/dependencies.example.json \
  --title "用户登录功能"
```

访问 [http://127.0.0.1:8500/ui](http://127.0.0.1:8500/ui) 查看 Consul 自带 UI。

#### 如何定义任务

任务在 `dependencies.json` 中定义，通过 `sync_to_consul.py` 同步到 Consul。每个任务的 `description` 字段告诉 Agent **要做什么**：

```json
{
  "backend": {
    "type": "backend",
    "depends_on": ["design"],
    "description": "实现用户登录 API：注册 / 登录 / 登出。参考：docs/login-api.md"
  },
  "test": {
    "type": "test",
    "depends_on": ["backend"],
    "description": "对 /api/login 做集成测试：正常登录、密码错误、账号不存在。测试文件：tests/test_login.py"
  }
}
```

**`description` 编写建议**：
- 直接说明目标：实现什么、测试什么、部署什么
- 引用具体文件或文档时给出路径
- 简洁但可执行 — Agent 会读取并自主执行

#### 增量添加任务

执行过程中，如果计划偏离预期，可以向已有 workflow 增量添加新任务，无需重新初始化。已完成的任务保持不变。

```bash
# 向已有 workflow 添加新任务
python scripts/add_task.py req-001 api-gateway \
  --description "实现认证 API 网关" \
  --type backend \
  --depends-on backend
```

**约束（双向检查）**：
1. 新任务的 `depends_on` 不能指向 `FAILED` 或 `ABORTED` 状态的任务（那些任务永远不会完成）
2. 已处于终止状态（DONE / FAILED / ABORTED）的现有任务不能依赖新任务（已完成的任务不会重新跑）

若所有依赖均为 DONE，新任务直接设为 PENDING；若依赖仍在执行中，新任务设为 BLOCKED。

#### 执行流程

框架内部自动执行的逻辑：

| 步骤 | 触发条件 | 执行动作 |
|------|----------|----------|
| **任务激活** | 所有依赖 DONE | Aggregator 将下游任务设为 PENDING |
| **任务执行** | Agent 领取 PENDING 任务 | Agent 写入 IN_PROGRESS → DONE |
| **故障恢复** | Agent 死亡或任务超时 | Watchdog 回滚为 PENDING（≤5次重试） |
| **质量门禁** | test 失败 | 等待所有 feedback.FIXED → 自动重测（≤3次重试） |
| **流程终止** | 所有任务 DONE | 流程结束；超过重试上限 → FAILED |

### 使用步骤

1. **定义依赖**：编写 `dependencies.json`，描述任务拓扑及依赖关系
2. **启动 Consul**：`./scripts/start_consul_dev.sh`
3. **启动框架**：`python -m harness_framework.daemon`
4. **初始化需求**：`python scripts/sync_to_consul.py <req_id> dependencies.json --title "需求标题"`
5. **查看状态**：访问 WebAPI 或 Consul UI 查看任务进度
6. **人工干预**：如需调整，通过 API 修改任务状态或重分配

### 企业级复杂应用

框架设计用于处理大规模企业级工作流，具备高并行性和高韧性。

**水平扩展**
- 运行多个框架实例（守护进程），每个实例使用不同端口，连接到同一个 Consul 集群
- 无单点瓶颈 — 每个实例独立操作不同的 workflow
- 任务级并行：DAG 中多个任务可同时执行

**多工作流编排**
- 支持同一个 Consul 集群中多个并发需求（`req-001`、`req-002`、...）
- 每个需求有独立的 DAG，通过前缀 `workflows/<req_id>/` 隔离
- Aggregator 自动发现并管理所有 workflow

**韧性与容错**
- Watchdog 独立检测每个任务的 Agent 崩溃和超时
- 自动回滚到 PENDING 并跟踪重试次数（最多 5 次重试）
- Aggregator 处理所有 feedback 修复后的重测逻辑（最多 3 次重试）
- Consul 的 Raft 一致性保证节点故障时状态持久化

**运营信心**
- 所有状态存储在 Consul KV — 易于检查、重放和调试
- 控制信号（PAUSE/RESUME/ABORT）支持随时人工干预
- REST API 提供聚合视图，看板无需查询原始 KV

**部署拓扑**

| 场景 | Consul | 框架实例数 |
|------|--------|-----------|
| 开发 | 单节点 dev mode | 1 个实例 |
| 预发 | 3 节点集群 | 1-2 个实例 |
| 生产 | 5 节点集群 + ACL | 多个实例，分布式 Agent |

### 配置项

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--consul` | `127.0.0.1:8500` | Consul HTTP 地址 |
| `--token` | （空） | Consul ACL Token（dev mode 可省略） |
| `--port` | `8080` | WebAPI 监听端口 |
| `--aggregator-interval` | `5` | DAG 推进轮询间隔（秒） |
| `--watchdog-interval` | `30` | 僵尸任务检测间隔（秒） |
| `--task-timeout` | `3600` | 单任务超时时间（秒） |

环境变量：`CONSUL_ADDR`、`CONSUL_TOKEN`

### Consul UI 与业务看板的关系

Consul 自带 UI 面向**运维**——查看原始 KV 内容、服务注册、健康状态。

Harness 业务看板面向**研发与产品**——DAG 拓扑可视化、任务进度、人工干预。

两者互补使用。

### 生产环境准备

从 dev mode 升级到生产 3 节点 Consul 集群时，业务代码无需改动，仅需修改 `--consul` 指向集群地址，并配置 ACL Token。
