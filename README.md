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

## 安装

```bash
# 框架核心零外部依赖（仅 Python 3.9+）
git clone <this-repo>
cd harness_framework

# 看板（可选）
cd agent_dashboard && npm install && cd ..
```
