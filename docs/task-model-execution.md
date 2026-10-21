# 按任务选择模型与会话

> **当前基线提示**：本文描述的是旧 Worker 的 profile/model 语义。新模型请使用 Task Capability Requirements、Execution Profile、Agent Runtime 和 Execution Manifest，详见 [实现设计](../DESIGN.md)。

Worker 可以在任务分派时选择执行 profile、模型和 provider 原生会话策略。
任务未配置 `execution` 时，Worker 默认延续唯一一个带原生会话的直接上游任务，
并继承它的 profile/model。没有可续接上游（例如根任务）时使用全局 `--executor`；
存在多个可续接上游时拒绝猜测，必须显式配置 `from_task`。

## 任务定义

启动独立会话：

```json
{
  "implement-login": {
    "type": "backend",
    "agent_name": "backend-agent",
    "service_name": "users",
    "depends_on": [],
    "execution": {
      "profile": "codex-high",
      "model": "configured-model-name",
      "session": {"mode": "new"}
    }
  }
}
```

延续上一个任务的原生会话：

```json
{
  "fix-login": {
    "type": "backend",
    "agent_name": "backend-agent",
    "service_name": "users",
    "depends_on": ["implement-login"],
    "execution": {
      "profile": "codex-high",
      "session": {
        "mode": "continue",
        "from_task": "implement-login"
      }
    }
  }
}
```

也可使用 `{"mode":"resume","session_id":"native-id"}` 恢复已知会话。
`continue` 从同一 workflow 的来源任务读取 `native_session_id`；来源任务尚未产生
该字段时，当前任务会失败并给出明确错误。

自动延续时，Worker 会把继承后的声明保存为 `execution_effective`，因此该策略可以
沿着多级任务链继续传播，而不只对紧邻显式配置的任务生效。

## Execution profile

复制 [execution-profiles.example.json](../examples/execution-profiles.example.json)
并按本机 wrapper 调整：

```bash
python skills/stage-bridge/scripts/worker.py \
  --name backend-agent \
  --service users \
  --execution-profiles /etc/harness/execution-profiles.json
```

也可以设置 `EXECUTION_PROFILES_FILE`。Profile 支持以下字段：

| 字段 | 说明 |
|------|------|
| `provider` | 会话命名空间，例如 `codex` |
| `command` | argv 数组；不会通过 shell 执行 |
| `args` | 每次调用都附加的参数 |
| `model` / `model_args` | 默认模型及参数模板 |
| `new_session_args` | 新会话附加参数 |
| `resume_session_args` | 恢复会话参数；支持 `{session_id}` |

参数模板支持 `{model}` 和 `{session_id}`。Profile 是 Worker 管理的可信配置；
任务可以覆盖 `model`，但不能把字符串交给 shell。

## Wrapper 协议

模型 CLI 的输入输出格式并不统一，所以 `command` 通常应指向一个轻量 wrapper。
Wrapper 从 stdin 读取 Worker 原有的 JSON task package，并在 stdout 只输出一个 JSON
对象：

```json
{
  "status": "DONE",
  "native_session_id": "provider-session-id",
  "artifact_refs": ["commit:abc123"]
}
```

失败返回 `{"status":"FAILED","error":"..."}`。新会话的 wrapper 应返回
`native_session_id`；恢复会话时即使省略，Worker 也会沿用输入中的 ID。

Worker 同时保存：

- `harness_session_id`：本次任务执行记录；
- `native_session_id`：Codex、Claude Code 等 provider 的原生会话；
- `execution_resolved/*`：实际 provider、model、profile 和 session mode。

恢复同一个 provider 原生会话时，Worker 使用带超时的 KV 锁避免两个任务并发写入。

## 增量分派

```bash
python scripts/add_task.py req-001 fix-login \
  --description "根据审查意见修复登录" \
  --type backend \
  --agent-name backend-agent \
  --service-name users \
  --depends-on implement-login \
  --execution-profile codex-high \
  --model configured-model-name \
  --session-mode continue \
  --session-from-task implement-login
```

如确需在任务中提供 argv，可使用 `--command-json`，并在 Worker 上通过
`--allowed-executables` 或 `ALLOWED_MODEL_EXECUTABLES` 明确授权可执行文件名。
未授权命令会在启动前被拒绝。生产环境推荐只开放命名 profile。

## 任务契约（do / input / output）

TASK PACKAGE 里除了任务元信息，还带结构化的节点契约：

```json
{
  "do": "这一步要做什么",
  "input": "消费什么",
  "output": "要产出什么",
  "output_format": {"kind": "marker", "pattern": "VERDICT:\\s*PASS"},
  "upstream": [{"task": "explore", "output": "proposal.md",
                "evidence": {"path": "proposal.md", "bytes": 812}}]
}
```

- `output_format` 支持 `file` / `json` / `marker` / `text`，由框架在 Agent
  结束这一轮后校验；**不符格式就视为该节点失败**
- 节点失败时，若声明了 `on_fail: {task, max_fail_count}`，Aggregator 会把
  目标节点重置为 PENDING 重跑；超过 `max_fail_count` 才判整个实例失败
- 校验通过的产物写入 `tasks/<task>/output`，下游节点通过 `upstream` 字段拿到

模板校验规则（有契约字段的模板）：

- 有前置步骤 → 必须声明 `input`
- 有后续步骤 → 必须声明 `output`
- 配置 `on_fail` → 必须声明 `output` 与 `output_format`

## 提示词开场白

开场白按执行范围收敛：

- 旧 workflow 范围（Agent 自己领任务、写产物/证据）**且**节点声明了
  `completion_contract` 时，才会提示用 stage-bridge 命令登记产物与证据
- 作业流范围不出现 stage-bridge（那里没有这条命令），门禁用节点契约的
  `output_format` 与 `VERDICT` 结论

作业流范围也不再校验旧式 `completion_contract`（没有写入通道，校验只会让任务
永远失败），改由 `output_format` / `require_verdict` 承担门禁。

## 会话查看（ACP）

对话正文只保存在 Agent 自己的会话存储里，框架 **不再复制** 到 KV。Dispatcher 只写映射：

| Key | 含义 |
|-----|------|
| `jobflows/instances/<id>/tasks/<task>/acp/provider` | 实际使用的 provider（`claude` / `codex` / `opencode`） |
| `.../acp/session_id` | Agent 原生会话 ID |
| `.../acp/cwd` | 会话启动时的执行目录 |
| `.../acp/attempt_id` / `.../acp/run_id` | 最近一次执行归属 |

看板通过 `GET /api/instances/<instance_id>/tasks/<task>/session` 读取映射，
凭 `provider + session_id + cwd` 连接 ACP 桥接（`ws://127.0.0.1:8787/acp`），
由 Agent 用 `session/load` 回放历史。页面上的「连接 / 断开」按需创建和释放
Agent 进程，断开后不会常驻。

任务执行中不允许连接（避免两个进程同时写同一个会话）；任务结束后可连接、
查看完整历史并继续对话。执行中的进度改用内存实时流：

- `GET /api/instances/<instance_id>/tasks/<task>/live?after=<seq>` 返回增量 ACP
  更新（`events[].payload.update`）、`cursor`、`active` 与 `truncated`。
- 数据只存在于 daemon 进程的内存环形缓冲（每任务 2000 条），不落 KV、不落盘；
  daemon 重启即清空，Agent 会话仍是唯一持久副本。
- 看板在任务 `PENDING` / `IN_PROGRESS` 时每 1.2 秒轮询一次，任务结束后停止，
  再通过「连接」用 `session/load` 读取完整历史。

> 历史由各 Agent 自己回放：`opencode acp` 会回放用户与助手文本，
> `codex-acp` 目前只回放工具调用。文本是否可读取决于 Agent 的
> `session/load` 实现，框架不做二次存储。
