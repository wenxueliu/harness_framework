# Harness Framework

**多 Agent 编排引擎** — 声明式定义工作流，自动推进依赖，故障自愈，可视化追踪。

Agent 认领任务 → 框架推进流程 → 你通过看板掌控全局。

## 一句话试试

```bash
python -m harness_framework.daemon --local
```

然后访问 [http://127.0.0.1:8080/api/health](http://127.0.0.1:8080/api/health) ，看到 `{"status": "ok"}` 就跑起来了。

> **下一步**：[5 分钟快速开始 → docs/quickstart.md](docs/quickstart.md)

## 解决什么问题

| 问题 | 框架怎么做 |
|------|-----------|
| **任务依赖混乱** | DAG 拓扑声明依赖，上游完成自动激活下游 |
| **Agent 崩溃任务卡死** | Watchdog 自动检测 → 回滚 → 重试 |
| **测试失败没人管** | 失败 → 通知修复 → 自动重测 |
| **重复执行污染外部系统** | Attempt fencing + idempotency key + compensation task |
| **长任务恢复丢进度** | 版本化 checkpoint + 新 attempt 自动恢复 |
| **需求变更全量返工** | ChangeSet 影响闭包 + 精准失效 + run roll-forward |
| **上下文泄漏或无限膨胀** | 分层 knowledge、显式 `context_inputs`、有界摘要与预算熔断 |
| **看不到进度** | WebAPI + 看板，所有状态一目了然 |

## 三种模式

```bash
python -m harness_framework.daemon --local-file   # 零依赖，最简启动
python -m harness_framework.daemon --local         # 内嵌 HTTP 服务器
python -m harness_framework.daemon                  # Consul 模式（生产）
```

详见 [docs/storage-modes.md](docs/storage-modes.md)。

## 文档导航

| 我想… | 看这个 |
|-------|--------|
| 3 分钟跑起来 | [quickstart.md](docs/quickstart.md) |
| 定义自己的工作流 | [getting-started.md](docs/getting-started.md) |
| 理解核心概念 | [concepts.md](docs/concepts.md) |
| 了解架构设计 | [architecture.md](docs/architecture.md) |
| 查配置项 | [configuration.md](docs/configuration.md) |
| 接入自己的 Agent | [agent-guide.md](docs/agent-guide.md) |
| 常见操作参考 | [usage-guide.md](docs/usage-guide.md) |
| 了解存储后端差异 | [storage-modes.md](docs/storage-modes.md) |
| 了解消息通信 | [message-bus.md](docs/message-bus.md) |
| 了解动态任务提案 | [proposal-protocol.md](docs/proposal-protocol.md) |
| 了解状态机细节 | [status-state-machine.md](docs/status-state-machine.md) |
| 了解动态任务设计 | [dynamic-tasks.md](docs/dynamic-tasks.md) |
| 了解重试与故障恢复 | [agent-retry-pattern.md](docs/agent-retry-pattern.md) |
| 了解记忆模型 | [memory-model.md](docs/memory-model.md) |
| 配置验证闭环 | [evaluator-loop.md](docs/evaluator-loop.md) |
| 在单个任务内运行 Executor–Reviewer 修订循环 | [internal-review-loop.md](docs/internal-review-loop.md) |
| 按任务选择模型并创建或延续原生会话 | [task-model-execution.md](docs/task-model-execution.md) |
| 在开发过程中修改需求并局部重跑 | [change-requirement.md](docs/change-requirement.md) |
| 使用证据驱动路由、原子动作与人工反馈 | [adaptive-control.md](docs/adaptive-control.md) |
| 查看可直接初始化的工作流样例 | [examples/README.md](examples/README.md) |
| 管理需求变更 | [changesets.md](docs/changesets.md) / [resource-versioning.md](docs/resource-versioning.md) |
| 处理失败与恢复 | [failure-envelope.md](docs/failure-envelope.md) |
| 保护外部副作用 | [side-effects.md](docs/side-effects.md) |
| 查看生产加固进度 | [production-hardening-status.md](docs/production-hardening-status.md) |
| 常见问题 | [faq.md](docs/faq.md) |

## 安装

```bash
# 框架核心零外部依赖（仅 Python 3.9+）
git clone <this-repo>
cd harness_framework

# 看板（可选）
cd agent_dashboard && npm install && cd ..
```
