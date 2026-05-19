---
name: file-kv
description: |
  Local-file mode KV CLI for agents running without Consul.
  When the framework starts with `--local-file`, all state is stored in a JSON file
  on disk. Agents use `file_kv.py` (not HTTP) to read/write KV, register, heartbeat,
  and manage the full agent lifecycle — no Consul, no HTTP server needed.
  Use when: local-file mode, no-Consul development, single-machine demo, offline test.
allowed-tools:
  - Bash
  - Read
---

# File KV — 纯文件存储 CLI

## 何时使用

当 Harness Framework 以 `--local-file` 模式启动时，**没有 HTTP 服务器**，Agent 不能通过 curl 或 `_consul.py` 读写状态。此时所有操作都通过 `file_kv.py` CLI 完成。

判断信号：框架启动参数包含 `--local-file`；环境变量 `HARNESS_STORE=file`；被告知使用纯文件模式。

## 数据文件

所有 KV 数据存储在单个 JSON 文件中，默认路径：

| 环境变量 | 默认值 |
|---------|--------|
| `FILE_STORE_DATA` | `~/.harness/file_store.json` |
| `--data-file` 参数 | 同上（优先级高于环境变量） |

数据文件由 `fcntl.flock` 保护并发写入，内部结构：

```json
{
  "data": {
    "workflows/req-001/title": "用户登录功能",
    "workflows/req-001/published": "true",
    "workflows/req-001/tasks/design/status": "DONE",
    ...
  },
  "agent_services": {
    "agent-001": {
      "ID": "agent-001",
      "Meta": { "capabilities": "backend", "service_name": "user-service" }
    }
  },
  "heartbeats": {
    "agent-001": "2026-05-20T10:00:00Z"
  },
  "_modify_index": 42
}
```

## 命令参考

### kv_get — 读取值

```bash
python scripts/file_kv.py get <key> [--recurse] [--data-file <path>]
```

- `--recurse`：返回 key 前缀下的所有键值（数组格式）
- 退出码 0 = 找到，1 = 未找到（兼容 Consul 语义）
- 输出格式模拟 Consul JSON 响应

示例：
```bash
# 读取单个值
python scripts/file_kv.py get workflows/req-001/tasks/design/status
# → [{"Key": "...", "Value": "RE5F", "ModifyIndex": 5}]

# 递归读取
python scripts/file_kv.py get workflows/req-001/ --recurse
```

### kv_put — 写入值

```bash
python scripts/file_kv.py put <key> <value> [--cas <index>] [--data-file <path>]
```

- `--cas <index>`：Check-And-Set，仅在 ModifyIndex 匹配时写入
- 输出 `true` / `false`，退出码 0=成功 1=CAS 失败

示例：
```bash
python scripts/file_kv.py put workflows/req-001/tasks/backend/status IN_PROGRESS --cas 5
```

### kv_delete — 删除键

```bash
python scripts/file_kv.py delete <key> [--recurse] [--data-file <path>]
```

- `--recurse`：删除 key 前缀下的所有键
- 无输出，退出码始终 0

示例：
```bash
python scripts/file_kv.py delete workflows/req-001/tasks/backend/status
python scripts/file_kv.py delete workflows/req-001/ --recurse   # 删除整个 workflow
```

### blocking-get — 阻塞等待变更

```bash
python scripts/file_kv.py blocking-get <key> [--index <n>] [--wait <30s>] [--data-file <path>]
```

- `--index <n>`：上次知道的 ModifyIndex，文件中的 index 大于此值时立即返回
- `--wait <30s>`：最长等待时间（支持 s/m/h 后缀，如 `60s`, `5m`）
- 超时未变更返回空 → 退出码 1

示例：
```bash
# 等待任务的 ModifyIndex 变化（Aggregator 激活检测用）
python scripts/file_kv.py blocking-get workflows/req-001/tasks/backend/status --index 5 --wait 60s
```

### register — Agent 注册

```bash
python scripts/file_kv.py register '<json-payload>' [--data-file <path>]
```

JSON payload 格式（兼容 Consul 服务注册）：

```json
{
  "ID": "agent-001",
  "Name": "agent-worker",
  "Tags": ["capability=backend", "service=user-service"],
  "Meta": {
    "agent_id": "agent-001",
    "capabilities": "backend",
    "service_name": "user-service",
    "repo_path": "/home/dev/user-service",
    "max_concurrent": "1",
    "current_load": "0"
  },
  "Check": {
    "CheckID": "service:agent-001",
    "TTL": "30s"
  }
}
```

示例：
```bash
python scripts/file_kv.py register '{"ID":"agent-001","Name":"agent-worker","Tags":["capability=backend","service=user-service"],"Meta":{"agent_id":"agent-001","capabilities":"backend","service_name":"user-service"}}'
```

### deregister — Agent 注销

```bash
python scripts/file_kv.py deregister <agent-id> [--data-file <path>]
```

从 `agent_services` 和 `heartbeats` 中移除该 Agent。

示例：
```bash
python scripts/file_kv.py deregister agent-001
```

### heartbeat — Agent 心跳

```bash
python scripts/file_kv.py heartbeat <agent-id> [--data-file <path>]
```

更新 Agent 的最后心跳时间。框架 Watchdog 通过检查心跳时间来判断 Agent 是否存活。

示例：
```bash
# 单次心跳
python scripts/file_kv.py heartbeat agent-001

# 循环心跳（后台进程）
while true; do python scripts/file_kv.py heartbeat agent-001; sleep 10; done
```

### list-services — 查看所有已注册 Agent

```bash
python scripts/file_kv.py list-services [--data-file <path>]
```

输出所有已注册的 agent_services 数组。

### status-leader — 模拟 Consul leader 检测

```bash
python scripts/file_kv.py status-leader [--data-file <path>]
```

始终输出 `"127.0.0.1:8300"`（兼容 `_consul.py` 的 `consul_health_check`）。

## Agent 生命周期（local-file 模式）

在 `--local-file` 模式下，Agent 的完整生命周期如下，所有操作通过 `file_kv.py` 完成：

### 1. 注册 + 心跳

```bash
export AGENT_ID=agent-$(hostname)-$$
REPO_PATH=$(pwd)

# 注册
python scripts/file_kv.py register "$(cat <<JSON
{"ID":"$AGENT_ID","Name":"agent-worker","Tags":["capability=backend","service=user-service"],"Meta":{"agent_id":"$AGENT_ID","capabilities":"backend","service_name":"user-service","repo_path":"$REPO_PATH","current_load":"0"}}
JSON
)"

# 后台心跳（每 10 秒）
while true; do python scripts/file_kv.py heartbeat "$AGENT_ID"; sleep 10; done &
```

### 2. 抢占任务

```bash
# 读取所有 PENDING 任务，按 service_name 匹配
python scripts/file_kv.py get workflows/ --recurse

# CAS 抢占
python scripts/file_kv.py put "workflows/$REQ_ID/tasks/$TASK_NAME/status" IN_PROGRESS \
  --cas "$(python scripts/file_kv.py get "workflows/$REQ_ID/tasks/$TASK_NAME/status" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['ModifyIndex'])")"
```

### 3. 读写上下文

```bash
# 读上游产物
python scripts/file_kv.py get workflows/$REQ_ID/context/ --recurse

# 写产物
python scripts/file_kv.py put "workflows/$REQ_ID/context/api_spec" '{"openapi":"3.0"}'

# 写步骤日志
python scripts/file_kv.py put "workflows/$REQ_ID/context/logs/step-1" "已完成接口设计"
```

### 4. 完成任务

```bash
python scripts/file_kv.py put "workflows/$REQ_ID/tasks/$TASK_NAME/status" DONE
```

### 5. 标记失败

```bash
python scripts/file_kv.py put "workflows/$REQ_ID/tasks/$TASK_NAME/status" FAILED
python scripts/file_kv.py put "workflows/$REQ_ID/tasks/$TASK_NAME/error_message" "测试未通过"
```

### 6. 注销

```bash
python scripts/file_kv.py deregister "$AGENT_ID"
```

## Agent 完整操作对照表

| 操作 | Consul / Local HTTP 模式 | Local-file 模式 |
|------|------------------------|-----------------|
| 注册 | `register_agent.py` | `file_kv.py register '...'` |
| 心跳 | `heartbeat.py --loop 10` | `while... file_kv.py heartbeat` |
| 抢占 | `claim_task.py` / `claim_next_task.py` | `file_kv.py get` + `file_kv.py put --cas` |
| 读上下文 | `read_context.py` | `file_kv.py get --recurse` |
| 写产物 | `write_artifact.py` | `file_kv.py put` |
| 完成 | `complete_task.py` | `file_kv.py put status DONE` |
| 失败 | `fail_task.py` | `file_kv.py put status FAILED` |
| 注销 | `deregister_agent.py` | `file_kv.py deregister` |
| 阻塞等待 | 不适用 | `file_kv.py blocking-get` |

## 注意事项

- **数据文件路径一致性**：框架启动参数 `--local-data-file <path>` 与 `file_kv.py --data-file <path>` 必须指向同一文件
- **并发安全**：FileStore 使用 `fcntl.flock`，多个进程可安全并发读写
- **无通知机制**：local-file 模式没有 watch/notify，Agent 需自己轮询或使用 `blocking-get`
- **性能限制**：JSON 文件全量读写，不适合高频写入（建议心跳间隔 ≥10s）
- **无 HTTP、无 curl** 所有操作都是本地 CLI，框架的 WebAPI 在此模式下不可用
