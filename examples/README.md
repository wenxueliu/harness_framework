# 示例目录

所有工作流示例都使用 `scripts/sync_to_consul.py` 接受的平铺 JSON 格式。

| 文件 | 用途 | 配套文档 |
|------|------|----------|
| `hello-world.json` | 单任务最小工作流 | [1 分钟快速上手](../docs/quickstart.md) |
| `simple-pipeline.json` | 完整 DAG、契约、预算、恢复和补偿 | [配置参考](../docs/configuration.md) |
| `internal-review.json` | Executor–Reviewer 修订循环 | [内部评审循环](../docs/internal-review-loop.md) |
| `adaptive-control.json` | 原子动作、检查证据和失败路由 | [自适应控制](../docs/adaptive-control.md) |
| `execution-profiles.example.json` | 模型命令和原生会话 profile | [任务模型执行](../docs/task-model-execution.md) |
| `sample-design.md` | 从设计文档生成 DAG 的输入样例 | [Agent 接入指南](../docs/agent-guide.md) |

例如，启动 `--local` 模式后初始化并发布自适应控制样例：

```bash
python scripts/sync_to_consul.py examples/adaptive-control.json --publish
```

示例内已声明 `req_id=adaptive-demo`，也可以用 `--req-id` 覆盖它。
