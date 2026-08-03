# 按任务选择模型与会话

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
