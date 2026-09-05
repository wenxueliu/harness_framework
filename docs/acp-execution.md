# ACP Agent 执行

Harness Framework 默认使用 [Agent Client Protocol](https://agentclientprotocol.com/) 主动执行 DAG
任务。无需预先启动、注册或轮询 Worker；当任务依赖满足并进入 `PENDING` 后，daemon 中的
`ACPDispatcher` 会原子接管任务，为这个步骤创建一个 Claude 或 Codex ACP Agent。

## 执行链路

```text
Aggregator: BLOCKED -> PENDING
                |
                v
ACPDispatcher: CAS -> IN_PROGRESS
                |
                +-- spawn claude-agent-acp / codex-acp (stdio)
                +-- initialize (ACP v1)
                +-- session/new 或 session/load
                +-- session/prompt(task package)
                +-- session/update -> Harness session events
                `-- end_turn -> completion contract -> DONE
```

ACP adapter 是 JSON-RPC 2.0 的 stdio 子进程。任务取消时 Dispatcher 发送
`session/cancel`；Agent 无需写 `agents/` 注册表。Watchdog 对 ACP 任务检查 attempt fencing、
可续租 lease 和 hard deadline，不检查 Consul service registration。

## 安装与认证

两个 adapter 默认由 `npx` 启动，需要 Node.js 22+：

```bash
npx -y @agentclientprotocol/claude-agent-acp --version
npx -y @agentclientprotocol/codex-acp --version
```

- Claude adapter 使用 Claude Agent SDK 的现有认证；可通过
  `npx -y @agentclientprotocol/claude-agent-acp --cli auth login` 登录。
- Codex adapter 使用 `~/.codex` 中的 ChatGPT 登录，也可设置 `CODEX_API_KEY` 或
  `OPENAI_API_KEY`。

参考实现：
[claude-agent-acp](https://github.com/agentclientprotocol/claude-agent-acp)、
[codex-acp](https://github.com/agentclientprotocol/codex-acp)。

## Agent 选择

未显式配置时按任务类型选择：

| 任务类型 | ACP Agent |
|---|---|
| `design`、`review` | Claude |
| `backend`、`frontend`、`test`、`deploy`、`task`、`generic` | Codex |

任务可覆盖默认选择：

```json
{
  "implement-login": {
    "type": "backend",
    "depends_on": ["design-login"],
    "description": "实现登录并补充测试",
    "acp": {
      "agent": "claude",
      "cwd": "/absolute/path/to/repository",
      "permission_policy": "allow_once",
      "session": {"mode": "new"}
    }
  }
}
```

`agent_name` 不再是必填调度键。保留该字段只用于关闭 ACP Dispatcher 后运行旧 Worker。

## Session 策略

默认每个任务建立新 session。也可以加载同一 provider 的历史 session：

```json
"acp": {
  "agent": "codex",
  "session": {"mode": "continue", "from_task": "implement-login"}
}
```

或者使用已知 ACP session ID：

```json
"acp": {
  "agent": "codex",
  "session": {"mode": "resume", "session_id": "provider-session-id"}
}
```

`continue` 从 `workflows/<req>/tasks/<from_task>/acp/session_id` 读取 ID，并调用
ACP `session/load`。来源任务必须使用同一 provider，且 adapter 必须支持 load session。

## daemon 配置

```bash
python -m harness_framework.daemon --local \
  --acp-workspace-root /absolute/path/to/repository \
  --acp-max-concurrency 4 \
  --acp-task-timeout 7200
```

| 参数 / 环境变量 | 默认值 | 说明 |
|---|---|---|
| `--acp-claude-command` / `ACP_CLAUDE_COMMAND` | `npx -y @agentclientprotocol/claude-agent-acp` | JSON argv 数组 |
| `--acp-codex-command` / `ACP_CODEX_COMMAND` | `npx -y @agentclientprotocol/codex-acp` | JSON argv 数组 |
| `--acp-routing` / `ACP_TASK_ROUTING` | 内置映射 | JSON 类型映射 |
| `--acp-workspace-root` / `ACP_WORKSPACE_ROOT` | daemon 当前目录 | 默认 session cwd |
| `--acp-max-concurrency` / `ACP_MAX_CONCURRENCY` | `4` | 最大并发任务数 |
| `--acp-task-timeout` / `ACP_TASK_TIMEOUT` | `7200` | prompt hard timeout |
| `--acp-permission-policy` / `ACP_PERMISSION_POLICY` | `allow_once` | `allow_once` 或 `deny` |
| `--no-acp-dispatcher` | 关闭 | 回退到旧注册/抢占 Worker |

命令参数必须是 JSON 数组，不经过 shell。例如使用全局安装的 adapter：

```bash
export ACP_CODEX_COMMAND='["codex-acp"]'
export ACP_CLAUDE_COMMAND='["claude-agent-acp"]'
```

## 完成与可观测性

- `session/update` 原样写入
  `workflows/<req>/sessions/<task>/<session>/events/`。
- ACP session ID 同时写入任务的 `acp/session_id` 与 `native_session_id`。
- 只有 `session/prompt` 返回 `stopReason=end_turn` 且 `completion_contract` 已满足时，任务才进入
  `DONE`；`max_tokens`、`refusal`、`cancelled` 或缺少 artifact/gate 都进入 `FAILED`。
- `/api/agents` 会把当前 ACP 子进程投影成 `acp,task-scoped` Agent，不依赖服务注册。

旧的 stage-bridge 注册/抢占脚本仍可用于迁移。启用旧 Worker 时必须给 daemon 加
`--no-acp-dispatcher`，避免两个执行器竞争同一个 `PENDING` 任务。
