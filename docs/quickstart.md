# 1 分钟快速上手

> 前提：Python 3.9+。不需要 Consul，不需要 pip install。

## Step 1：启动框架

```bash
python -m harness_framework.daemon --local
```

终端输出 `LocalStore 已启动` 即代表成功。保持这个终端开着。

> `--local` 模式在内存中运行，同时在 8500 端口启动一个内嵌 HTTP 服务器（兼容 Consul API）。Agent 和脚本都可以通过 HTTP 连接。

## Step 2：初始化工作流

另开一个终端，执行：

```bash
python scripts/sync_to_consul.py examples/hello-world.json \
  --req-id hello-001 --title "Hello World" --publish
```

这个命令创建了一个工作流 `hello-001`，包含一个任务 `hello`，且已发布。

## Step 3：查看结果

```bash
# WebAPI 查询所有工作流状态
curl -s http://127.0.0.1:8080/api/workflows | python3 -m json.tool

# 查询 hello-001 详情
curl -s http://127.0.0.1:8080/api/workflow/hello-001 | python3 -m json.tool

# 健康检查
curl -s http://127.0.0.1:8080/api/health
```

你会看到 `hello` 任务状态为 `PENDING`——Aggregator 检测到它无依赖，已自动激活。

## 发生了什么

```
你定义 hello-world.json       →  框架写入 KV
hello 无依赖                  →  Aggregator 设为 PENDING
Agent 认领执行                →  IN_PROGRESS → DONE
```

## 下一步

| 我想… | 看这里 |
|-------|--------|
| 定义有依赖的工作流（design → backend → test） | [5 分钟入门 →](getting-started.md) |
| 理解 DAG、状态机、Agent 协作原理 | [核心概念 →](concepts.md) |
| 把 Agent 接入框架执行任务 | [Agent 接入指南 →](agent-guide.md) |
| 查看所有配置项和 CLI 参数 | [配置参考 →](configuration.md) |
| 尝试证据驱动检查和失败恢复 | [自适应控制示例 →](adaptive-control.md#可运行示例失败后回到实现任务) |
| 浏览其他工作流样例 | [示例目录 →](../examples/README.md) |
