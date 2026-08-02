# Agent 接入指南

> **初次接触？** 先看 [quickstart.md](quickstart.md) 和 [concepts.md](concepts.md)。本文是 Agent 接入的完整指南。

这篇文章告诉你如何把自己的 AI Agent 接入 Harness Framework。

## 前置：安装 Skill

在接入 Agent 之前，需要先将框架的 Skill 安装到你的项目目录。框架提供 8 个 Skill，封装了 Agent 与框架交互的完整逻辑。

使用 `install.py` 一键安装：

```bash
# 安装全部 8 个 Skill 到当前目录（Claude Code 模式）
python /path/to/harness_framework/install.py

# 只安装 4 个核心 Skill（stage-bridge, task-executor, file-kv, harness-sync）
python /path/to/harness_framework/install.py --minimal

# 安装到指定项目
python /path/to/harness_framework/install.py --target /path/to/your/project

# 安装到 Codex 平台
python /path/to/harness_framework/install.py --codex
```

安装完成后，你的项目下会出现 `skills/` 目录，包含以下 Skill：

| 框架运行模式 | 使用的 Skill | 职责 |
|------------|-------------|------|
| `--local-file`（单机） | `file-kv` | 纯文件 KV 读写，无需注册/心跳 |
| `--local` / Consul | `stage-bridge` | 完整生命周期：注册→心跳→抢占→执行→完成→注销 |

## 前提理解

Agent 和框架的协作模型是**共享状态**：Agent 读写 KV 存储，框架轮询 KV 存储。双方不直接通信。

```
Agent ──写──→ KV 存储 ←──读── 框架
```

Agent 需要做的事（完整生命周期）：
1. **注册**（单机模式跳过）
2. **心跳**（单机模式跳过）
3. **认领任务**
4. **执行任务**
5. **完成任务**（或标记失败）
6. **注销**（单机模式跳过）

## Agent→框架的交互方式

Agent 与框架交互有三种方式，按推荐优先级排列：

| 优先级 | 方式 | 适用场景 |
|--------|------|---------|
| ⭐ **推荐** | **Skill 方式** | Agent 平台支持 Skill 系统（Claude Code、Codex、OpenCode 等） |
| ✅ 可选 | **脚本方式** | Agent 不支持 Skill，但可执行 Python 脚本或 Shell 命令 |
| 🔧 扩展 | **裸 HTTP 调用** | 自定义 Agent，需要最底层的控制 |

三种方式对应相同的底层 KV 操作语义，只是封装层次不同。

## 方式一（推荐）：Skill 方式

框架提供两个 Skill，封装了 Agent 与框架交互的完整逻辑。Agent 只需加载对应 Skill，由 Skill 自动处理生命周期。

| 框架运行模式 | 使用的 Skill | 职责 |
|------------|-------------|------|
| `--local-file`（单机） | `file-kv` | 纯文件 KV 读写，无需注册/心跳 |
| `--local` / Consul | `stage-bridge` | 完整生命周期：注册→心跳→抢占→执行→完成→注销 |

**Agent 不需要关心底层是 Consul HTTP 还是文件 CLI**——Skill 封装了所有差异。如果有新的存储后端，只需更新 Skill，Agent 不受影响。

### 如何加载 Skill

Skill 不会自动注入 Agent。由**操作者**（启动 Agent 的人或系统）根据框架运行模式，将对应的 SKILL.md 文件内容提供给 Agent：

| 框架模式 | 加载方式 | 提供给 Agent 的内容 |
|---------|---------|-------------------|
| Claude Code | 加载 Skill（`/skill load` 或 Skill 工具） | `skills/file-kv/SKILL.md` 或 `skills/stage-bridge/SKILL.md` |
| Codex / OpenCode | 技能系统加载 | 同上 |
| 通用 LLM | 直接贴入 System Prompt | SKILL.md 全文 |
| 脚本调用 | 不加载 Skill，直接跑脚本（见方式二） | 不需要 SKILL.md |

**判断规则**：框架以 `--local-file` 启动 → 用 `file-kv` Skill；框架以 `--local` 或默认 Consul 模式启动 → 用 `stage-bridge` Skill。

> 如果 Agent 平台没有 Skill 机制（不支持动态加载 skill 文件），操作者可以直接把 SKILL.md 的内容粘贴到 Agent 的系统提示词中，效果相同。

### 单机模式 + file-kv Skill

框架以 `--local-file` 启动时（自动启用单机模式），操作者将 `skills/file-kv/SKILL.md` 加载给 Agent，Agent 通过 `file_kv.py` CLI 操作 JSON 文件，无需注册和心跳。

#### 环境变量

```bash
export AGENT_ID=my-agent
export DATA_FILE=~/.harness/file_store.json
```

#### Agent 提示词示例

先加载 `skills/file-kv/SKILL.md` 给 Agent，再附上以下指令（只需告诉它做什么，怎么做由 Skill 负责）：

```markdown
你是 Harness Framework 的开发 Agent，已加载 **file-kv** skill。

- Agent ID: `my-agent`
- 数据文件: `~/.harness/file_store.json`

## 工作流程

1. 扫描 workflows/ 下的 PENDING 任务，CAS 抢占一个匹配你的任务
2. 读 context/ 获取上游产物，执行任务
3. 将产出写入 context/，记录步骤日志
4. 完成任务（status DONE）或标记失败（status FAILED + error_message）

你无需注册、心跳、注销。Watchdog 默认认为你始终存活。
```

> 单机模式下无需 `register`、`heartbeat`、`deregister`。Watchdog 会把 `standalone-agent` 视为始终存活。

### HTTP / Consul 模式 + stage-bridge Skill

框架以 `--local` 或默认 Consul 模式启动时，操作者将 `skills/stage-bridge/SKILL.md` 加载给 Agent，Skill 自动管理注册、心跳、抢占、完成、注销的完整生命周期。

#### 环境变量

```bash
export CONSUL_ADDR=127.0.0.1:8500
export AGENT_ID=my-agent
export SERVICE_NAME=myservice
export REPO_PATH=/path/to/your/repo
```

#### Agent 提示词示例

先加载 `skills/stage-bridge/SKILL.md` 给 Agent，再附上以下指令：

```markdown
你是 Harness Framework 的开发 Agent，已加载 **stage-bridge** skill。

- Agent ID: `my-agent`
- 服务名: `myservice`
- Consul 地址: `127.0.0.1:8500`

## 工作流程

1. 注册到 Consul，启动后台心跳
2. 轮询 PENDING 任务，抢占一个 service_name 匹配你的任务
3. 读 context/ 获取上游产物，执行任务，记录日志，写产物
4. 完成任务或标记失败
5. 完成后注销

每次 LLM 调用前后检查 ABORT 信号，收到则立即退出。
```

#### Skill 自动管理的完整流程

```
启动 → 注册 → 启动心跳 → 轮询 PENDING → 抢占 → 执行 → 完成 → 注销
```

## 方式二：脚本方式

如果 Agent 平台不支持 Skill 系统，可以直接调用 Python 脚本完成相同操作。这是 Skill 方式的底层实现，语义完全一致。

### 单机模式（file_kv.py）

脚本：`scripts/file_kv.py`

```bash
# 1. 认领 PENDING 任务（CAS 原子抢占）
python scripts/file_kv.py --data-file $DATA_FILE \
  get workflows/myapp-001/tasks/backend/status

# 如果状态是 PENDING，抢占
python scripts/file_kv.py --data-file $DATA_FILE \
  put workflows/myapp-001/tasks/backend/status IN_PROGRESS
python scripts/file_kv.py --data-file $DATA_FILE \
  put workflows/myapp-001/tasks/backend/assigned_agent $AGENT_ID

# 2. 执行任务（Agent 做事）...

# 3. 完成任务
python scripts/file_kv.py --data-file $DATA_FILE \
  put workflows/myapp-001/tasks/backend/status DONE
```

脚本的完整命令参考见 `skills/file-kv/SKILL.md`。

### HTTP / Consul 模式（stage-bridge 脚本）

脚本：`skills/stage-bridge/scripts/`

```bash
# 注册
python skills/stage-bridge/scripts/register_agent.py

# 心跳（后台持续，每 10 秒一次）
python skills/stage-bridge/scripts/auto_register.py --daemon &

# 认领指定任务
python skills/stage-bridge/scripts/claim_task.py myapp-001 backend

# 记录日志
python skills/stage-bridge/scripts/log_step.py myapp-001 "开始实现 /api/tasks 端点"

# 完成任务
python skills/stage-bridge/scripts/complete_task.py myapp-001 backend \
  --meta '{"commit":"abc123"}'

# 标记失败
python skills/stage-bridge/scripts/fail_task.py myapp-001 backend \
  --error "数据库连接超时"

# 注销
python skills/stage-bridge/scripts/auto_register.py --stop
```

## 方式三：裸 HTTP 调用

对于自定义 Agent 或需要更底层控制的场景，可以直接用 curl 或任何 HTTP 客户端操作 Consul KV API。

```bash
# 认领任务（CAS 抢占）
# 先读当前状态和 ModifyIndex
curl -s http://127.0.0.1:8500/v1/kv/workflows/req-001/tasks/backend/status

# CAS 写入 IN_PROGRESS
curl -s -X PUT \
  --data-binary IN_PROGRESS \
  "http://127.0.0.1:8500/v1/kv/workflows/req-001/tasks/backend/status?cas=42"

# 完成任务
curl -s -X PUT \
  --data-binary DONE \
  "http://127.0.0.1:8500/v1/kv/workflows/req-001/tasks/backend/status"

# 标记失败
curl -s -X PUT \
  --data-binary FAILED \
  "http://127.0.0.1:8500/v1/kv/workflows/req-001/tasks/backend/status"
```

## Task Agent 的工作模式

当一个 Agent 绑定到某个 `service_name` 后，它只认领匹配该 service_name 的任务。Agent 不关心 DAG 的全貌——它只需要知道"有没有我能做的 PENDING 任务"。

## 单任务内接入独立 Reviewer

紧密耦合的代码审查可以通过 `review_policy` 放在当前任务内部。Worker 使用 `--executor` 执行业务工作，再用 `--reviewer` 发送结构化 Review Package；`CHANGES_REQUIRED` 会作为下一轮 Executor 的 `review_feedback`，直到 PASS 或达到最大轮数。

Reviewer 判断问题源于上游时，可以从任务配置的 `allowed_recovery_targets` 中选择 `recovery_target`。框架会回退该上游任务、阻塞其下游，并将 findings 作为目标 Agent 的 `recovery_feedback`；不允许跳转到无关任务。

```bash
python skills/stage-bridge/scripts/worker.py \
  --service user-service \
  --executor "python /opt/agents/executor.py" \
  --reviewer "python /opt/agents/reviewer.py"
```

完整协议、配置和人工 approve/reject API 见 [单任务 Executor–Reviewer 闭环](internal-review-loop.md)。需要独立排期或合规隔离的验收仍应定义为单独 DAG 任务。

## 任务完成后

Agent 完成任务后，框架的 Aggregator 会在下一次轮询时（默认 5 秒内）检测到，并自动激活所有依赖该任务的下游任务。Agent 可以继续认领新的 PENDING 任务。

## 下一步

| 我想… | 看这里 |
|-------|--------|
| 了解 stage-bridge Skill（HTTP/Consul 模式） | [stage-bridge SKILL.md](../skills/stage-bridge/SKILL.md) |
| 了解 file-kv Skill（单机文件模式） | [file-kv SKILL.md](../skills/file-kv/SKILL.md) |
| 理解故障恢复机制（超时、Agent 死亡） | [核心概念 →](concepts.md) |
| 了解任务间消息通信（Message Bus） | [消息总线 →](message-bus.md) |
| 定义复杂 DAG（并行、聚合节点） | [架构设计 →](architecture.md) |
| 配置任务内 Review 与人工确认 | [Executor–Reviewer 闭环 →](internal-review-loop.md) |
