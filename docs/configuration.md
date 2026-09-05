# 配置参考

> **初次接触？** 先看 [quickstart.md](quickstart.md)。本文是 CLI 参数和环境变量的速查参考。

## 启动参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--consul` | `$CONSUL_ADDR` 或 `127.0.0.1:8500` | Consul 地址 |
| `--token` | `$CONSUL_TOKEN` 或空 | Consul ACL Token |
| `--host` | `0.0.0.0` | WebAPI 监听地址 |
| `--port` | `8080` | WebAPI 端口 |
| `--aggregator-interval` | `5` | Aggregator 轮询间隔（秒） |
| `--watchdog-interval` | `30` | Watchdog 轮询间隔（秒） |
| `--task-timeout` | `120` | 单任务超时时间（秒） |
| `--heartbeat-timeout` | `120` | Agent 心跳超时（秒） |
| `--max-retry` | `3` | 任务最大重试次数 |
| `--log-level` | `INFO` | 日志级别：DEBUG/INFO/WARNING/ERROR |
| `--log-dir` | 空（仅 stdout） | 日志目录 |
| `--log-max-bytes` | `10485760` | 单个日志文件最大字节数 |
| `--log-backup-count` | `5` | 保留的旧日志文件数量 |

### 存储模式

| 参数 | 说明 |
|------|------|
| `--local` | 内存存储 + 内嵌 HTTP 服务器 |
| `--local-port` | 内嵌 HTTP 服务器端口（默认 8500） |
| `--local-data-file` | JSON 持久化文件路径（默认 `~/.harness/local_store.json`） |
| `--local-file` | 纯文件模式（自动启用单机模式） |
| `--standalone` | 单机模式：Agent 无需注册/心跳/注销 |
| `--standalone-agent-id` | 默认 Agent ID（默认 `standalone-agent`） |

### 组件开关

| 参数 | 说明 |
|------|------|
| `--no-aggregator` | 禁用 Aggregator |
| `--no-watchdog` | 禁用 Watchdog |
| `--no-webapi` | 禁用 WebAPI |
| `--no-acp-dispatcher` | 禁用默认 ACP 主动分派，使用旧 Worker 兼容模式 |

### ACP 执行

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--acp-workspace-root` | 当前目录 | Agent session 默认工作目录 |
| `--acp-max-concurrency` | `4` | 并发 ACP Agent 上限 |
| `--acp-task-timeout` | `7200` | 单次 ACP prompt 超时秒数 |
| `--acp-permission-policy` | `allow_once` | 权限请求策略：`allow_once` / `deny` |
| `--acp-claude-command` | Claude adapter 的 npx argv | JSON argv 数组 |
| `--acp-codex-command` | Codex adapter 的 npx argv | JSON argv 数组 |
| `--acp-routing` | 内置类型映射 | task type → `claude`/`codex` JSON 对象 |

## 环境变量

| 变量 | 说明 |
|------|------|
| `CONSUL_ADDR` | Consul 地址（被 `--consul` 覆盖） |
| `CONSUL_TOKEN` | Consul ACL Token（被 `--token` 覆盖） |
| `ACP_CLAUDE_COMMAND` / `ACP_CODEX_COMMAND` | adapter JSON argv |
| `ACP_TASK_ROUTING` | task type 到 provider 的 JSON 映射 |
| `ACP_WORKSPACE_ROOT` | 默认 Agent 工作目录 |
| `ACP_MAX_CONCURRENCY` / `ACP_TASK_TIMEOUT` | 并发数与执行超时 |
| `ACP_PERMISSION_POLICY` | `allow_once` 或 `deny` |

**优先级**：命令行参数 > 环境变量

## 任务级生产策略

以下字段写在 `dependencies.json` 的任务定义中，由
`scripts/sync_to_consul.py` 校验并下发：

| 字段 | 用途 | 关键约束 |
|------|------|----------|
| `acp` | 指定 `claude`/`codex`、cwd、权限和 session 策略 | 可选；缺省按任务类型路由 |
| `agent_name` | 旧 Worker 的逻辑 Agent Name | 仅兼容模式使用，不再必填 |
| `service_name` | 标记业务或代码仓库归属 | 可选；不参与 Agent 匹配 |
| `agent_contract` | 输入、输出、职责、排除项、权限、上下文预算 | 列表字段必须为非空字符串列表 |
| `completion_contract` | required artifacts 与 verifier gates | 未满足时拒绝 `DONE` |
| `review_policy` | 单任务内独立 Reviewer、最大修订轮数与人工确认 | 启用时 `completion_contract.required_gates` 必须包含 `review` |
| `context_inputs` | 精确选择 facts/artifacts/summaries | 缺省为空；不能通配 restricted/events |
| `evaluator_policy` | 最大迭代、平台期、fallback、升级 | fallback 名称唯一且有序 |
| `resource_budget` | token、cost、tool call、wall clock 上限 | 任一越界打开 circuit breaker |
| `recovery_policy` | primary/narrowed/degraded/human 路径 | 尝试次数必须为非负整数 |
| `side_effecting` | 声明任务会修改外部状态 | 为 true 时必须配置下面两项 |
| `idempotency_scope` | 业务幂等键作用域 | side-effecting task 必填 |
| `compensation_task` | 失败后的补偿任务 | 目标必须存在且为 `compensation_only` |
| `activation` | `normal` 或 `compensation_only` | 补偿任务不会被 Aggregator 正常激活 |
| `execution` | 按任务选择模型命令和原生会话策略 | 使用 profile；支持 `new`、`continue`、`resume` |

每个任务初始化时还会写入独立的 `validity=UNKNOWN`。正常完成或通过检查后
变为 `VALID`；需求变更或自适应恢复会把受影响的 DAG 下游闭包标记为
`INVALIDATED`。执行状态和结论有效性是两个不同维度，详见
[自适应控制](adaptive-control.md)。

### ACP Agent 路由

默认 `design/review → claude`，其他可执行类型 → `codex`。任务用
`"acp":{"agent":"claude"}` 覆盖；完整说明见 [ACP Agent 执行](acp-execution.md)。

### 旧 Worker 名称匹配（兼容模式）

`agent_name` 是调度键，`service_name` 不是。Agent 注册时同时提供：

```bash
export AGENT_ID=backend-agent-01   # 运行实例 ID：租约、心跳、审计
export AGENT_NAME=backend-agent    # 逻辑名称：必须匹配任务 agent_name

python skills/stage-bridge/scripts/register_agent.py \
  --name "$AGENT_NAME" --capabilities backend
```

自动抢占、指定任务抢占和常驻 Worker 都在 CAS 前检查名称；名称不一致时不会
写入 `IN_PROGRESS`。同一个 Agent Name 可以有多个实例 ID，最终仍由 CAS 决定
哪一个实例获得任务。

在旧兼容模式中，所有可执行任务必须显式写 `agent_name`，所有 Agent 必须通过 `--name` 或
`AGENT_NAME` 显式注册逻辑名称。缺少名称时配置、注册或领取直接失败；
`service_name` 永远不会被当作调度名称。

示例：

```json
{
  "deploy": {
    "type": "deploy",
    "agent_name": "deploy-agent",
    "service_name": "users",
    "depends_on": ["test"],
    "context_inputs": ["artifacts/release", "facts/region"],
    "resource_budget": {
      "max_tokens": 50000,
      "max_cost_usd": 10,
      "max_tool_calls": 100,
      "max_wall_clock_seconds": 3600
    },
    "side_effecting": true,
    "idempotency_scope": "deployment",
    "compensation_task": "rollback"
  },
  "rollback": {
    "type": "deploy",
    "agent_name": "deploy-agent",
    "service_name": "users",
    "depends_on": [],
    "activation": "compensation_only"
  }
}
```

完整配置实例见 [simple-pipeline.json](../examples/simple-pipeline.json)。

### 自适应路由预算

路由预算是运行期状态，不写在任务定义里。可在工作流启动后通过 Consul 兼容
KV API 覆盖默认值：

```bash
curl -s -X PUT \
  http://127.0.0.1:8500/v1/kv/workflows/adaptive-demo/routing/budget \
  -d '{
    "policy": {
      "max_total_routes": 4,
      "max_same_edge_routes": 2,
      "max_same_failure_fingerprint": 2
    },
    "state": {"total": 0, "edges": {}, "fingerprints": {}}
  }'
```

未配置时依次使用默认上限 `8`、`2`、`2`。任一预算耗尽后，当前任务进入
`WAITING_FOR_HUMAN`，避免无限恢复循环。完整样例见
[adaptive-control.json](../examples/adaptive-control.json)。

### Worker 模型执行参数

| 参数 / 环境变量 | 说明 |
|-----------------|------|
| `--execution-profiles` / `EXECUTION_PROFILES_FILE` | 命名 execution profile JSON 文件 |
| `--allowed-executables` / `ALLOWED_MODEL_EXECUTABLES` | 允许任务直接指定的可执行文件名，逗号分隔 |
| `--executor` | 根任务或没有可续接上游会话时使用的兼容 executor |

配置格式和安全边界见[按任务选择模型与会话](task-model-execution.md)。

## 常用启动组合

```bash
# 最简启动（单机、零依赖、零心跳）
python -m harness_framework.daemon --local-file

# 开发调试（HTTP 通信、日志详细）
python -m harness_framework.daemon --local --log-level DEBUG

# 生产模式（Consul 后端）
python -m harness_framework.daemon --consul consul-cluster:8500 --token $CONSUL_TOKEN

# 仅跑调度，不要心跳检测（单机全自动）
python -m harness_framework.daemon --local-file --no-watchdog

# 高超时任务
python -m harness_framework.daemon --task-timeout 3600 --max-retry 5
```

## 相关文档

| 我想… | 看这里 |
|-------|--------|
| 快速上手 | [quickstart.md →](quickstart.md) |
| 了解存储后端差异 | [storage-modes.md →](storage-modes.md) |
| Agent 接入指南 | [agent-guide.md →](agent-guide.md) |
| 常见操作命令 | [usage-guide.md →](usage-guide.md) |
| 配置证据检查、路由预算和人工反馈 | [adaptive-control.md →](adaptive-control.md) |
