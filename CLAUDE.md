# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 概述

`harness-framework` 是多 Agent 协作的**核心引擎**，解决分布式 Agent 之间的流程控制、状态管理与反馈闭环问题。核心能力：

- **流程控制**：基于 DAG 拓扑的任务依赖调度，依赖满足自动激活下游任务
- **状态管理**：统一的任务状态机（BLOCKED → PENDING → IN_PROGRESS → DONE/FAILED），支持人工干预（PAUSE/RESUME/ABORT）
- **反馈闭环**：test 失败时等待所有 feedback FIXED 后自动重测，形成"失败→修复→验证"的完整闭环
- **容错恢复**：Agent 死亡或任务超时时自动回滚重试，保障任务最终完成
- **人工接管**：任何时刻可人工介入（重分配任务、强制状态变更），实现人机协同

## 架构

```
harness_framework/
├── daemon.py          # 主进程入口，启动 Aggregator + Watchdog + WebAPI 线程
├── aggregator.py      # 监听 DAG 状态变更，依赖满足时激活下游任务
├── watchdog.py        # 检测 IN_PROGRESS 任务的 Agent 存活和超时，自动恢复
├── webapi.py          # HTTP API 为业务看板提供聚合查询与控制信号写入
├── message_bus.py     # 任务间消息通信（发送、轮询、完成）
└── consul_client.py   # Consul HTTP 客户端（仅标准库，无外部依赖）
```

**三大组件：**
- **Aggregator**：仅处理 `published=true` 的 workflow，轮询任务状态，当依赖全部 DONE 时将下游任务设为 PENDING；当 test 任务 FAILED 且所有 feedback FIXED 时触发重测。
- **Watchdog**：仅处理 `published=true` 的 workflow，轮询 Consul Health 检测 Agent 是否存活，检测任务超时（默认 1h），超时或 Agent 死亡时将任务回滚为 PENDING（最多 5 次重试，超过则 FAILED）。
- **WebAPI**：基于标准库 `http.server` 的 ThreadingHTTPServer，提供 `/api/workflows`、`/api/workflow/<req_id>`、`/api/agents` 等端点。

## 使用步骤

1. **定义依赖**：编写 `dependencies.json`，描述任务拓扑及依赖关系
2. **启动 Consul**：`./scripts/start_consul_dev.sh`
3. **启动框架**：`python -m harness_framework.daemon`
4. **初始化需求**：`python scripts/sync_to_consul.py <req_id> dependencies.json --title "需求标题"`
5. **查看状态**：访问 WebAPI 或 Consul UI 查看任务进度
6. **人工干预**：如需调整，通过 API 修改任务状态或重分配

## 执行流程

框架内部自动执行的核心逻辑：

1. **任务激活**：Aggregator 检测所有依赖 DONE → 激活下游任务为 PENDING
2. **任务执行**：Agent 领取 PENDING 任务 → 写入 IN_PROGRESS → 执行完成写入 DONE
3. **故障恢复**：Watchdog 检测超时/Agent 死亡 → 回滚任务为 PENDING（≤5次重试）
4. **质量门禁**：test 失败 → 等待 feedback.FIXED → 自动重测（≤3次重试）
5. **流程终止**：所有任务 DONE → 流程结束；超过重试上限 → FAILED

## Consul KV 结构

```
workflows/<req_id>/
├── published               # true | false（草稿模式，默认 false），仅发布后 watchdog/aggregator 才会处理
├── title                   # 需求标题
├── priority                # 整数优先级，越大越优先，默认 0
├── control                 # 控制信号：PAUSE | RESUME | ABORT
├── dependencies            # JSON，任务依赖拓扑
├── created_at
├── tasks/<task_name>/
│   ├── status              # PENDING | BLOCKED | IN_PROGRESS | DONE | FAILED | ABORTED | AWAITING_REVIEW
│   ├── type                # design | review | backend | test | deploy
│   ├── service_name        # 关联的服务名（可选）
│   ├── description
│   ├── assigned_agent
│   ├── started_at / activated_at / retry_count / error_message
│   └── last_recovery_reason / last_recovery_at
├── feedback/<service>/
│   └── status              # FIXED | OPEN
└── context/...             # 任意上下文键值
```

## 任务类型与状态流转

**任务类型**（`type` 字段）：`design`、`review`、`backend`、`test`、`deploy`

**核心状态**：空白/已终止任务初始为 `BLOCKED`（叶子任务直接 `PENDING`）；`IN_PROGRESS` 由 Agent 手动写入；所有依赖 DONE 时由 Aggregator 激活为 `PENDING`。

**Aggregator 重测逻辑**：test 任务 FAILED → 检查所有 feedback.status == FIXED → 清除 feedback → 重置任务为 PENDING → retry_count++（Aggregator 上限 3 次）

**Watchdog 恢复逻辑**：Agent 死亡或任务超时 → retry_count++ → retry_count >= 5 则 FAILED，否则回滚为 PENDING（Watchdog 上限 5 次）

## 任务间消息通信

详见 [docs/message-bus.md](docs/message-bus.md)。

## 常用命令

consul 安装在 consul_server

# 启动 consul server
consul_server/consul agent -server -ui -bootstrap-expect=1 --node harness_framework_master -data-dir="consul_server/data" -bind="127.0.0.1" -client="0.0.0.0"
```bash
# 启动 Consul dev mode
./scripts/start_consul_dev.sh

# 启动框架主进程（默认 8080 端口）
python -m harness_framework.daemon

# 指定端口和其他参数
python -m harness_framework.daemon --port 9000 --consul 127.0.0.1:8500 --task-timeout 1800

# 初始化一个需求（写入 Consul）
python scripts/sync_to_consul.py req-001 examples/dependencies.example.json --title "用户登录功能"

# 带日志级别启动
python -m harness_framework.daemon --log-level DEBUG
```

**配置方式**：命令行参数 > 环境变量 `CONSUL_ADDR` / `CONSUL_TOKEN`

## 代码风格

- 仅使用 Python 标准库
- 类型注解使用 `from __future__ import annotations`
- 日志格式：`%(asctime)s [%(name)s] %(levelname)s %(message)s`

## 测试

### 测试文件结构

```
tests/
├── __init__.py
├── conftest.py              # 公共 fixtures（MockConsulStore, mock_consul）
├── test_consul_client.py    # ConsulClient 单元测试
├── test_aggregator.py       # Aggregator 单元测试
├── test_watchdog.py         # Watchdog 单元测试
├── test_webapi.py           # WebAPI 单元测试
└── test_message_bus.py      # MessageBus 单元测试
```

### 运行测试

```bash
# 运行所有测试
python -m pytest tests/ -v

# 运行指定模块
python -m pytest tests/test_aggregator.py -v

# 带覆盖率
python -m pytest tests/ --cov=harness_framework
```

### 测试数据构造

**MockConsulStore**：内存 KV 模拟，接受初始字典 `{"key": "value"}`。
通过 `conftest.py` 中的 `mock_consul` fixture 注入到各模块。

**Aggregator 测试数据示例**：

```python
store = {
    "workflows/req-001/dependencies": json.dumps({
        "design": {"type": "design", "depends_on": []},
        "backend": {"type": "backend", "depends_on": ["design"]},
        "test": {"type": "test", "depends_on": ["backend"]},
    }),
    "workflows/req-001/tasks/design/status": "DONE",
    "workflows/req-001/tasks/backend/status": "BLOCKED",
    "workflows/req-001/tasks/test/status": "BLOCKED",
}
```

**Watchdog 测试数据示例**：

```python
# Agent 死亡场景
alive_agents = {"agent-001"}
task = {
    "status": "IN_PROGRESS",
    "assigned_agent": "agent-002",  # 不在 alive list
    "started_at": "2025-04-22T10:00:00",
    "retry_count": "1",
}

# 超时场景（started_at 3小时前）
old_time = (datetime.utcnow() - timedelta(hours=3)).isoformat() + "Z"
task = {
    "status": "IN_PROGRESS",
    "assigned_agent": "agent-001",
    "started_at": old_time,
    "retry_count": "0",
}
```

**WebAPI 测试数据示例**：

```python
# GET /api/workflows
store = {
    "workflows/req-001/title": "登录功能",
    "workflows/req-001/tasks/design/status": "DONE",
    "workflows/req-001/tasks/backend/status": "IN_PROGRESS",
    "workflows/req-002/tasks/design/status": "DONE",
    "workflows/req-002/tasks/backend/status": "DONE",
}
# 预期: req-001 phase=RUNNING progress=50.0
#       req-002 phase=DONE progress=100.0

# POST /api/workflow/req-001/control
{"action": "PAUSE"}     # → kv_put("workflows/req-001/control", "PAUSE")
{"action": "RESUME"}   # → kv_delete("workflows/req-001/control")
{"action": "RETRY", "task_name": "backend"}  # → backend 重置为 PENDING
{"action": "INVALID"}  # → 400
```

### 测试策略

- **UT**：mock ConsulClient，用 `MockConsulStore` 模拟 KV 读写，验证状态流转逻辑
- **E2E**：启动真实 Consul + daemon，构造 workflow 后验证 Consul KV 状态变更

# Superpowers + gstack 搭配配置

## Superpowers（思考与流程层）
负责所有 plan、brainstorm、debug、TDD、verify、code review。
触发方式：自动触发。


## gstack（执行与外部世界层）
负责浏览器操作、QA、ship、deploy、canary、安全审计。
触发方式：斜杠命令手动触发。

## 浏览器规则
使用 /browse 作为唯一浏览器入口。
禁止使用 mcp__claude-in-chrome__* 操作浏览器。

## 分工裁决
- 计划撰写 → Superpowers: writing-plans
- 计划多视角审查 → gstack: /autoplan
- 编码 → Superpowers: test-driven-development
- 调试 → Superpowers: systematic-debugging
- 真实环境验证 → gstack: /qa
- 代码审查 → Superpowers: requesting-code-review
- 发布 → gstack: /ship
- 安全审计 → gstack: /cso

## Web Browsing

Available gstack skills:
- `/office-hours` - Schedule office hours
- `/plan-ceo-review` - CEO review planning
- `/plan-eng-review` - Engineering review planning
- `/plan-design-review` - Design review planning
- `/design-consultation` - Design consultation
- `/design-shotgun` - Design shotgun
- `/design-html` - Design HTML
- `/review` - Code review
- `/ship` - Ship feature
- `/land-and-deploy` - Land and deploy
- `/canary` - Canary deployment
- `/benchmark` - Benchmark
- `/browse` - Web browsing
- `/connect-chrome` - Connect Chrome
- `/qa` - QA testing
- `/qa-only` - QA only
- `/design-review` - Design review
- `/setup-browser-cookies` - Setup browser cookies
- `/setup-deploy` - Setup deployment
- `/retro` - Retrospective
- `/investigate` - Investigate
- `/document-release` - Document release
- `/codex` - Codex
- `/cso` - CSO
- `/autoplan` - Auto plan
- `/plan-devex-review` - DevEx review planning
- `/devex-review` - DevEx review
- `/careful` - Careful mode
- `/freeze` - Freeze
- `/guard` - Guard
- `/unfreeze` - Unfreeze
- `/gstack-upgrade` - Upgrade gstack
- `/learn` - Learn
