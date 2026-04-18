# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 概述

`agent-platform` 是一个多 Agent 软件开发平台的**框架层**，在一个 Python 进程中通过线程并发运行三大组件，所有协作状态存储在 **Consul KV** 中。

## 架构

```
agent_platform/
├── daemon.py          # 主进程入口，启动 Aggregator + Watchdog + WebAPI 线程
├── aggregator.py      # 监听 DAG 状态变更，依赖满足时激活下游任务
├── watchdog.py        # 检测 IN_PROGRESS 任务的 Agent 存活和超时，自动恢复
├── webapi.py          # HTTP API 为业务看板提供聚合查询与控制信号写入
└── consul_client.py   # Consul HTTP 客户端（仅标准库，无外部依赖）
```

**三大组件：**
- **Aggregator**：轮询 `workflows/<req_id>/tasks/*/status`，当依赖全部 DONE 时将下游任务设为 PENDING；当 test 任务 FAILED 且所有 feedback FIXED 时触发重测。
- **Watchdog**：轮询 Consul Health 检测 Agent 是否存活，检测任务超时（默认 1h），超时或 Agent 死亡时将任务回滚为 PENDING（最多 5 次重试，超过则 FAILED）。
- **WebAPI**：基于标准库 `http.server` 的 ThreadingHTTPServer，提供 `/api/workflows`、`/api/workflow/<req_id>`、`/api/agents` 等端点。

## 常用命令

```bash
# 启动 Consul dev mode
./scripts/start_consul_dev.sh

# 启动框架主进程（默认 8080 端口）
python -m agent_platform.daemon

# 指定端口和其他参数
python -m agent_platform.daemon --port 9000 --consul 127.0.0.1:8500 --task-timeout 1800

# 初始化一个需求（写入 Consul）
python scripts/sync_to_consul.py req-001 examples/dependencies.example.json --title "用户登录功能"

# 带日志级别启动
python -m agent_platform.daemon --log-level DEBUG
```

**配置方式**：命令行参数 > 环境变量 `CONSUL_ADDR` / `CONSUL_TOKEN`

## Consul KV 结构

```
workflows/<req_id>/
├── title                    # 需求标题
├── control                  # 控制信号：PAUSE | RESUME | ABORT
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

## 代码风格

- 零外部依赖，仅使用 Python 标准库
- 类型注解使用 `from __future__ import annotations`
- 日志格式：`%(asctime)s [%(name)s] %(levelname)s %(message)s`
