# 配置参考

> **初次接触？** 先看 [quickstart.md](quickstart.md)。本文是 CLI 参数和环境变量的速查参考。
>
> 当前实现以 [ACP 执行设计](acp-execution.md) 和 [实现设计](../DESIGN.md) 为准。本文中旧 Worker、`agent_name`、`--no-acp-dispatcher` 和 stage-bridge 相关段落仅用于识别待清理配置，不应在新作业流中使用。

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
| `--no-acp-dispatcher` | 已废弃；新实现不提供旧 Worker / stage-bridge 回退路径 |

### ACP 执行

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--acp-workspace-root` | 当前目录 | Agent session 默认工作目录 |
| `--acp-max-concurrency` | `4` | 并发 ACP Agent 上限 |
| `--acp-task-timeout` | `7200` | 单次 ACP prompt 超时秒数 |
| `--acp-permission-policy` | `allow_once` | 权限请求策略：`allow_once` / `deny` |
| `--acp-claude-command` | Claude adapter 的 npx argv | JSON argv 数组 |
| `--acp-codex-command` | Codex adapter 的 npx argv | JSON argv 数组 |
| `--acp-opencode-command` | `["opencode", "acp"]`（原生 ACP，无需 npx） | JSON argv 数组 |
| `--acp-routing` | 内置类型映射 | task type → `claude`/`codex`/`opencode` JSON 对象 |

支持的 provider 固定为 `claude`、`codex`、`opencode`。配置了其他值时，
模板校验会返回 `INVALID_AGENT`，错误信息里带上实际值与可选值列表
（例如 `节点 build 的 Agent 不受支持: opencode2（可选: claude, codex, opencode）`），
daemon 日志同样记录该具体名称。

## 环境变量

| 变量 | 说明 |
|------|------|
| `CONSUL_ADDR` | Consul 地址（被 `--consul` 覆盖） |
| `CONSUL_TOKEN` | Consul ACL Token（被 `--token` 覆盖） |
| `ACP_CLAUDE_COMMAND` / `ACP_CODEX_COMMAND` / `ACP_OPENCODE_COMMAND` | adapter JSON argv |
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
| `acp` | 指定 `claude`/`codex`/`opencode`、cwd、权限和 session 策略 | 可选；缺省按任务类型路由 |
| `agent_name` | 历史逻辑 Agent 名称 | 新模型不作为调度键；使用 capability requirements + Agent Runtime |
| `service_name` | 标记业务或代码仓库归属 | 可选；不参与 Agent 匹配 |
| `context_inputs` | 精确选择 facts/artifacts/summaries | 缺省为空；不能通配 restricted/events |
| `evaluator_policy` | 最大迭代、平台期、fallback、升级 | fallback 名称唯一且有序 |
| `resource_budget` | token、cost、tool call、wall clock 上限 | 任一越界打开 circuit breaker |
| `recovery_policy` | primary/narrowed/degraded/human 路径 | 尝试次数必须为非负整数 |
| `side_effecting` | 声明任务会修改外部状态 | 为 true 时必须配置下面两项 |
| `idempotency_scope` | 业务幂等键作用域 | side-effecting task 必填 |
| `compensation_task` | 失败后的补偿任务 | 目标必须存在且为 `compensation_only` |
| `activation` | `normal` 或 `compensation_only` | 补偿任务不会被 Aggregator 正常激活 |
| `execution` | 按任务选择模型命令和原生会话策略 | 使用 profile；支持 `new`、`continue`、`resume` |
| `do` | 这一步要做什么（执行指令） | 与 `description` 至少有一个；发给 Agent |
| `input` | 这一步消费什么 | 可选；缺失时提示词不含该段描述 |
| `output` | 这一步要产出什么 | 可选；缺失时下游提示词不含该段描述 |
| `output_format` | 产物的机器可校验格式 | `file` / `json` / `marker` / `text`；不符即视为该节点失败 |
| `on_fail` | 失败时回退到哪个节点重来 | `{task, max_fail_count}`；目标节点必须存在，且本节点必须配置 `output_format` |

产物流向：节点通过校验后，产物记录写入 `tasks/<task>/output`，下游节点的
下游节点的 `input` 里以 `[上游产物]` 形式带给 Agent。

每个任务初始化时还会写入独立的 `validity=UNKNOWN`。正常完成或通过检查后
变为 `VALID`；需求变更或自适应恢复会把受影响的 DAG 下游闭包标记为
`INVALIDATED`。执行状态和结论有效性是两个不同维度，详见
[自适应控制](adaptive-control.md)。

### ACP Agent 路由

默认 `design/review → claude`，其他可执行类型 → `codex`。任务用
`"acp":{"agent":"claude"}` 覆盖；可用 provider 为 `claude`、`codex`、
`opencode`（`opencode` 走原生 `opencode acp`，无需 npx 下载 adapter）。
完整说明见 [ACP Agent 执行](acp-execution.md)。

> 注意：Execution Profile 的 `agent_runtime_id` 只参与 Preflight 检查和 Manifest
> 快照，**不参与**实际 dispatch 路由；节点未配置 `acp.agent` 时按上述类型路由表
> 执行，不会回退到 Profile 绑定。详见 [ACP Agent 执行](acp-execution.md) 第 5 节。

### 执行目录

同一个模板可以被多个实例复用，而每个实例通常运行在不同的代码目录。Agent 进程的
`cwd` 按以下优先级解析（高 → 低）：

1. **节点级**：任务的 `acp.cwd` 或顶层 `repo_path`（`acp.cwd` 优先）。节点级目录
   优先于 managed workspace，即显式目录会覆盖 Run 的 attempt 绑定目录。
2. **managed workspace**：实例以 `workspace` 策略启动时，使用该 Run 的 attempt 绑定目录。
3. **实例级**：`create_instance` 请求体的 `directory` 字段（存入实例 context，`rerun` 会继承）。
4. **全局**：`--acp-workspace-root`（`ACP_WORKSPACE_ROOT`）。

显式目录（节点/实例）建议使用绝对路径；相对路径按 daemon 进程 cwd 解析。显式目录
不存在时任务进入 `WAITING_FOR_HUMAN`（原因 `WORKSPACE_UNAVAILABLE`），不会静默回退。

> 前端「创建实例」向导在「执行目录」提供两种模式：使用 Run Workspace（默认，
> managed workspace 生效）或指定已有目录（发送 `workspace: {}` + `directory`，
> 不创建 managed workspace）。实例详情页展示最终配置的实例级目录。

```bash
# 实例级：整个实例默认在 /code/user-service 下执行
curl -X POST http://localhost:8080/api/instances \
  -H 'Idempotency-Key: create-1' \
  -d '{"template_id":"tpl","directory":"/code/user-service"}'
```

```json
// 节点级：仅该节点切到另一个目录
{ "id": "docs", "type": "backend", "depends_on": ["build"],
  "repo_path": "/code/docs-site" }
```

### 历史 Worker 名称匹配（不再用于新模型）

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
