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

| Feature | Benefit |
|---------|---------|
| **Zero external Python deps** | Ships with standard library only, minimal footprint |
| **Consul-based state** | Durable, observable, easy to inspect and debug |
| **Auto-recovery** | Tasks automatically restart on agent death or timeout |
| **Bidirectional API** | Read state + write control signals (pause/resume/abort) |

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

### Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
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

### How to Contribute

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Make your changes (remember: zero external Python dependencies)
4. Run tests if available
5. Commit with a clear message
6. Open a Pull Request

For major changes, please open an issue first to discuss.

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

| 特性 | 价值 |
|------|------|
| **零外部 Python 依赖** | 仅使用标准库，部署简单 |
| **基于 Consul 的状态存储** | 持久化、可观测、易于调试 |
| **自动恢复** | Agent 死亡或超时时任务自动重启 |
| **双向 API** | 支持读取状态 + 写入控制信号（暂停/恢复/中止） |

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

### 如何贡献

1. Fork 本仓库
2. 创建功能分支：`git checkout -b feature/your-feature`
3. 进行修改（记住：零外部 Python 依赖）
4. 如有测试，运行测试
5. 提交并附上清晰的 commit message
6. 提交 Pull Request

重大变更请先开 issue 讨论。
