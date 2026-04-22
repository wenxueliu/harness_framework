---
name: stage-bridge
description: 多 Agent 开发平台的 Consul 状态桥接 Skill。当编码智能体（如 Codex / OpenCode / Claude Code）作为执行层 Agent 加入多 Agent 软件开发流程时，加载此 Skill 以读写 Consul KV、抢占任务、记录日志、传递上下文、上报失败反馈。仅用于已部署 stage-bridge 框架（含 Consul dev mode）的本地开发环境。
---

# stage-bridge — 多 Agent 状态桥接 Skill

## 何时使用

读取本 Skill 当且仅当你被指派为 **多 Agent 开发平台的某种执行层 Agent**（设计 / 评审 / 开发 / 测试 / 部署等），且需要与其他 Agent 通过 Consul 协作。如果你只是单独执行一个独立的编码任务，不需要本 Skill。

判断信号包括：环境变量中存在 `CONSUL_ADDR`、`AGENT_ID`、`REQ_ID`、`TASK_NAME` 中的一个或多个；用户/调度方明确告知你是某需求的某个 Agent；你被要求上报进度、读取上游产物或写入失败反馈。

## Consul 地址

默认 `127.0.0.1:8500`，可通过环境变量 `CONSUL_ADDR` 覆盖。**所有 curl 命令中的 `$CONSUL_ADDR` 即为该地址**。

## Consul KV 路径约定

```
workflows/<req_id>/
  ├── tasks/<task_name>/        ← 任务状态、产物、元数据
  │   ├── status                ← PENDING / IN_PROGRESS / DONE / FAILED / AWAITING_REVIEW
  │   ├── assigned_agent        ← 实际执行 Agent 的 ID
  │   └── <自定义 key>           ← 任务产物
  ├── context/                  ← 需求级上下文（跨任务共享）
  ├── sessions/<task>/<session_id>/events/<seq>  ← 执行日志事件流
  ├── feedback/<service>/       ← 测试反馈给服务 Agent
  └── control                   ← PAUSE / RESUME / ABORT 控制信号
```

## 完整生命周期

下表是任意类型 Agent 的标准协作流程。操作以 curl 为主，**心跳使用 Python 后台脚本**。

| 阶段 | curl 命令 | 说明 |
| :--- | :--- | :--- |
| 注册 | 见下方 Agent 注册一节 | 一键注册 Agent 到 Consul |
| 心跳 | `heartbeat.py --loop 10` | 维持 TTL check，每 10 秒一次，后台持续运行 |
| 停止心跳 | `deregister_agent.py` | 注销 Agent |
| 抢占（指定） | `claim_task.sh <req_id> <task_name>` | 抢占指定任务 |
| 抢占（自动） | `claim_next_task.sh` | 自动查找并抢占下一个可用任务 |
| 列出任务 | `claim_next_task.sh --list-only` | 仅查看所有 PENDING 任务 |
| 读上下文 | `read_context.sh <req_id> [<key>]` | 抢占成功后读取上游产物 |
| 记录日志 | `log_step.sh <req_id> "<message>"` | 任意关键步骤完成后 |
| 写产物 | `write_artifact.sh <req_id> <key> <value>` | 产生需向下游传递的数据 |
| 完成 | `complete_task.sh <req_id> <task_name>` | 任务成功完成 |
| 失败 | `fail_task.sh <req_id> <task_name> --error "..."` | 不可恢复错误 |
| ABORT 检查 | `check_control.sh <req_id>` | LLM 调用前后 / verify 每轮 / feedback 唤醒时必检，收到 ABORT 立即退出 |
| 写反馈 | `feedback_write.sh <req_id> <service> --error "..."` | 测试 Agent 专用 |
| 听反馈 | `feedback_listen.sh <req_id> <service>` | 服务 Agent 专用：监听修复请求 |
| 解反馈 | `feedback_resolve.sh <req_id> <service> --summary "..."` | 服务 Agent 专用：完成修复 |
| 查需求状态 | 见 Proposal 一节 | 检查需求当前状态 |
| 提出新任务 | 见 Proposal 一节 | Agent 发现遗漏任务时提出提案 |
| 确认提案 | 见 Proposal 一节（人工） | 接受或拒绝新任务提案 |

必传环境变量：`AGENT_ID`（全局唯一）；任务相关命令额外需要 `REQ_ID`、`TASK_NAME`。

## .env 配置文件

除环境变量外，脚本支持从 `.env` 文件读取配置。配置优先级：**命令行参数 > 环境变量 > .env 文件 > 默认值**。

`.env` 文件搜索路径（按顺序）：
1. 当前工作目录：`./.env`
2. skill 目录：`skills/stage-bridge/.env`
3. 固定目录：`~/.claude/stage-bridge/.env`

**CONSUL_ADDR 默认值**：若 .env 和环境变量中均未设置，自动使用 `127.0.0.1:8500`，并写入 `./.env`（若该文件不存在则创建）。

**AGENT_ID**：必须由用户显式指定，无默认值。

示例 `.env` 文件（自动生成）：
```bash
# Consul 连接（默认值，若不存在会自动写入）
CONSUL_ADDR=127.0.0.1:8500

# Agent 配置（需用户显式设置）
AGENT_ID=my-agent
SERVICE_NAME=user-service
REPO_PATH=/path/to/your/service

# 任务配置（可选）
REQ_ID=req-001
TASK_NAME=implement-api
```

首次使用时，若 `.env` 不存在，脚本会自动创建并写入 `CONSUL_ADDR=127.0.0.1:8500`，无需用户确认。

## 错误处理与退出码

- 退出码 **0**：操作成功
- 退出码 **1**：业务错误（如任务非 PENDING 或被抢先），应视为正常分支按业务逻辑处理
- 退出码 **2**：系统错误（如 Consul 不可达），应重试 3 次后中止任务
- 退出码 **7**：收到 ABORT 信号，立即退出

## Agent 注册与心跳

### 注册 Agent

```bash
# 构造注册 payload
curl -s -X PUT "http://$CONSUL_ADDR/v1/agent/service/register" \
  -H "Content-Type: application/json" \
  -d "{
    \"ID\": \"$AGENT_ID\",
    \"Name\": \"agent-worker\",
    \"Tags\": [\"capability=backend\", \"env=local\"],
    \"Meta\": {
      \"agent_id\": \"$AGENT_ID\",
      \"service_name\": \"$SERVICE_NAME\",
      \"repo_path\": \"$REPO_PATH\"
    },
    \"Check\": {
      \"CheckID\": \"service:$AGENT_ID\",
      \"Name\": \"TTL check for $AGENT_ID\",
      \"TTL\": \"30s\",
      \"DeregisterCriticalServiceAfter\": \"2m\"
    }
  }"

# 同时写入 KV
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/agents/$AGENT_ID/load" -d "0"
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/agents/$AGENT_ID/registered_at" -d "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

### 心跳（每 10 秒，后台 Python 脚本）

心跳必须持续发送，建议使用 Python 脚本在后台运行：

```bash
# 后台循环心跳（每 10 秒一次）
AGENT_ID="$AGENT_ID" python3 skills/stage-bridge/scripts/heartbeat.py --loop 10 &
```

### 注销 Agent

```bash
curl -s -X PUT "http://$CONSUL_ADDR/v1/agent/service/deregister/$AGENT_ID"
curl -s -X DELETE "http://$CONSUL_ADDR/v1/kv/agents/$AGENT_ID?recurse=true"
```

## 任务抢占与完成（curl 版）

### 抢占指定任务（CAS）

```bash
# 1. 读取当前状态和 ModifyIndex
STATUS=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/$TASK_NAME/status?raw")
INDEX=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/$TASK_NAME/status" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['ModifyIndex'])")

# 2. CAS 写入 IN_PROGRESS（若返回 true 则抢占成功）
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/$TASK_NAME/status?cas=$INDEX" -d "IN_PROGRESS"

# 3. 写入抢占元数据
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/$TASK_NAME/assigned_agent" -d "$AGENT_ID"
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/$TASK_NAME/started_at" -d "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

### 抢占下一个可用任务

```bash
# 1. 列出所有 PENDING 任务
curl -s "http://$CONSUL_ADDR/v1/kv/?recurse=true" | python3 -c "
import sys, json, base64
items = json.load(sys.stdin)
for it in items:
    if '/tasks/' in it['Key'] and it['Key'].endswith('/status'):
        v = base64.b64decode(it['Value']).decode() if it['Value'] else ''
        if v == 'PENDING':
            print(it['Key'].replace('/status','').replace('workflows/',''))
"

# 2. 按上述 CAS 流程抢占
```

### 完成任务

```bash
# 写入产物元数据
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/$TASK_NAME/meta" -d '{...}'
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/$TASK_NAME/completed_by" -d "$AGENT_ID"
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/$TASK_NAME/status" -d "DONE"
```

### 标记失败

```bash
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/$TASK_NAME/error_message" -d "$ERROR_MSG"
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/$TASK_NAME/failed_at" -d "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/$TASK_NAME/status" -d "FAILED"
```

## 上下文读写（curl 版）

### 读上下文

```bash
# 读所有上下文
curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/context/?recurse=true"

# 读指定 key
curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/context/$KEY?raw"
```

### 写产物（task 级）

```bash
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/$TASK_NAME/$KEY" -d "$VALUE"
```

### 写产物（context 级）

```bash
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/context/$KEY" -d "$VALUE"
```

## 记录日志（curl 版）

```bash
SEQ=$(date +%s%3N)
MSG='{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","agent_id":"'"$AGENT_ID"'","level":"info","message":"'"$MESSAGE"'"}'
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/sessions/$TASK_NAME/$SESSION_ID/events/$SEQ" -d "$MSG"
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/sessions/$TASK_NAME/$SESSION_ID/latest_event" -d "$MSG"
```

## ABORT 检查（curl 版）

```bash
CONTROL=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/control?raw")
if [ "$CONTROL" = "ABORT" ]; then
    echo "收到 ABORT 信号，退出"
    exit 7
fi
```

## 反馈（curl 版）

### 测试 Agent 写反馈

```bash
PAYLOAD='{"reporter":"'"$AGENT_ID"'","ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","service":"'"$SERVICE"'","error_summary":"'"$ERROR"'","severity":"high"}'
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/feedback/$SERVICE/payload" -d "$PAYLOAD"
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/feedback/$SERVICE/status" -d "PENDING_FIX"
```

### 服务 Agent 听反馈（阻塞）

```bash
# 阻塞等待（Consul blocking query）
curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/feedback/$SERVICE/status?index=0&wait=30s"
# 检查 status 是否为 PENDING_FIX
```

### 服务 Agent 完成修复

```bash
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/feedback/$SERVICE/fix_summary" -d "$SUMMARY"
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/feedback/$SERVICE/fixer" -d "$AGENT_ID"
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/feedback/$SERVICE/status" -d "FIXED"
```

## Dependencies Proposal（动态任务提案）

Agent 在执行过程中发现需要新增任务（如测试发现遗漏工作）时，可以通过 Proposal 机制向人工申请。

### 需求状态机

```
DRAFT → CONFIRMED → IN_PROGRESS → DONE
         ↑
       Proposal（中间态）
```

| 状态 | 含义 |
|------|------|
| DRAFT | 草稿，尚未启动 |
| Proposal | 有 Agent 发现需要新任务，暂停调度，等待人工确认 |
| CONFIRMED | 人工确认后，正常调度 |
| IN_PROGRESS | 执行中 |

### Agent 检查需求状态

```bash
# 读取需求当前状态
curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status?raw"
# 返回: DRAFT | Proposal | CONFIRMED | IN_PROGRESS | DONE
```

### Agent 等待 Proposal 解决（阻塞轮询）

```bash
# 循环检查直到状态不再是 Proposal
while true; do
  STATUS=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status?raw")
  if [ "$STATUS" != "Proposal" ]; then
    echo "Proposal 已解决，当前状态: $STATUS"
    break
  fi
  echo "等待人工确认提案，休息 5 秒..."
  sleep 5
done
```

### Agent 提出新任务（Proposal）

```bash
# 1. 读取当前 dependencies
DEPS=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/dependencies?raw")

# 2. 用 jq 或 python 添加新任务（示例：添加 perf-opt 任务）
echo "$DEPS" | python3 -c "
import sys, json
deps = json.load(sys.stdin)
deps['perf-opt'] = {
    'type': 'backend',
    'depends_on': ['build-user-service'],
    'proposed_by': '$TASK_NAME',
    'reason': '测试发现性能瓶颈，需要优化',
    'description': '性能优化：登录接口响应时间 > 200ms'
}
print(json.dumps(deps, ensure_ascii=False))
" > /tmp/new_deps.json

# 3. 写回 dependencies
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/dependencies" -d @/tmp/new_deps.json

# 4. CAS 设置状态为 Proposal（若已有 Proposal 则跳过）
INDEX=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['ModifyIndex'])")
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status?cas=$INDEX" -d "Proposal"

# 5. 写入提案元数据
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/perf-opt/proposed_by" -d "$TASK_NAME"
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/perf-opt/proposed_at" -d "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# 6. 退出当前任务，等人工确认
echo "提案已提交，等待人工确认..."
exit 0
```

### 查看所有待确认的提案

```bash
# 从 dependencies 中筛选 proposed_by 不为空的任务
curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/dependencies?raw" | python3 -c "
import sys, json
deps = json.load(sys.stdin)
for task, info in deps.items():
    if info.get('proposed_by'):
        print(f\"任务: {task}\")
        print(f\"  提出者: {info['proposed_by']}\")
        print(f\"  提出时间: {info.get('proposed_at', 'N/A')}\")
        print(f\"  依赖: {info.get('depends_on', [])}\")
        print(f\"  类型: {info.get('type', 'task')}\")
        print(f\"  原因: {info.get('reason', 'N/A')}\")
        print()
"
```

### 人工确认或拒绝提案

```bash
# === 确认全部提案 ===
INDEX=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['ModifyIndex'])")
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status?cas=$INDEX" -d "CONFIRMED"

# === 拒绝指定提案（从 dependencies 中删除） ===
DEPS=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/dependencies?raw")
echo "$DEPS" | python3 -c "
import sys, json
deps = json.load(sys.stdin)
deps.pop('perf-opt', None)  # 删除被拒绝的任务
print(json.dumps(deps, ensure_ascii=False))
" > /tmp/deps_filtered.json
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/dependencies" -d @/tmp/deps_filtered.json
INDEX=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['ModifyIndex'])")
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status?cas=$INDEX" -d "CONFIRMED"

# === 仅接受部分提案 ===
DEPS=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/dependencies?raw")
echo "$DEPS" | python3 -c "
import sys, json
deps = json.load(sys.stdin)
# 删除所有 proposed_by 的任务，重新写入只保留不需要人工确认的
keep = {k: v for k, v in deps.items() if not v.get('proposed_by')}
print(json.dumps(keep, ensure_ascii=False))
" > /tmp/deps_accepted.json
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/dependencies" -d @/tmp/deps_accepted.json
INDEX=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['ModifyIndex'])")
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status?cas=$INDEX" -d "CONFIRMED"
```

### 流程总结

1. **Agent 发现遗漏** → 提出新任务（status → Proposal）
2. **Aggregator 暂停调度** → 等待人工确认
3. **人工确认** → status → CONFIRMED，新任务按依赖激活
4. **人工拒绝** → 删除提案，status → CONFIRMED，Agent 可重新发起

## 三类 Agent 的典型工作流

### 开发 Agent

```bash
# === 启动阶段 ===
# 注册
curl -s -X PUT "http://$CONSUL_ADDR/v1/agent/service/register" \
  -H "Content-Type: application/json" \
  -d "{\"ID\":\"$AGENT_ID\",\"Name\":\"agent-worker\",\"Tags\":[\"capability=backend\"],\"Check\":{\"CheckID\":\"service:$AGENT_ID\",\"TTL\":\"30s\",\"DeregisterCriticalServiceAfter\":\"2m\"}}"

# 心跳（后台 Python 脚本，每 10 秒一次）
AGENT_ID="$AGENT_ID" python3 skills/stage-bridge/scripts/heartbeat.py --loop 10 &

# === 任务执行阶段 ===
# 抢占任务（见上方 CAS 流程）

# 读取上下文
curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/context/?recurse=true"

# 记录日志
SEQ=$(date +%s%3N)
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/sessions/$TASK_NAME/$SESSION_ID/events/$SEQ" \
  -d '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","agent_id":"'"$AGENT_ID"'","level":"info","message":"开始实现功能"}'

# 写产物
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/context/pr_url" -d "https://..."

# 完成任务
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/$TASK_NAME/status" -d "DONE"

# === 注销 ===
curl -s -X PUT "http://$CONSUL_ADDR/v1/agent/service/deregister/$AGENT_ID"
```

### 测试 Agent

```bash
# 抢占测试任务
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/test-e2e/status?cas=$INDEX" -d "IN_PROGRESS"

# 执行测试...

# 失败时归因到具体服务
PAYLOAD='{"reporter":"test-agent","ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","service":"user-service","error_summary":"Login API returns 500"}'
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/feedback/user-service/payload" -d "$PAYLOAD"
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/feedback/user-service/status" -d "PENDING_FIX"

curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/test-e2e/status" -d "FAILED"
```

### 服务 Agent 接收反馈

```bash
# 监听反馈
STATUS=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/feedback/user-service/status?raw")

# 智能体根据反馈自主诊断 + 修复...

# 完成修复
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/feedback/user-service/fix_summary" -d "修复了 /api/login 的 NPE 异常"
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/feedback/user-service/fixer" -d "$AGENT_ID"
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/feedback/user-service/status" -d "FIXED"
```

框架的 Aggregator 会自动检测：当所有 service 的反馈状态都是 FIXED 时，会清除反馈并将测试任务重置为 PENDING，触发重新测试。

## 重要约束

- 任务一旦被抢占成功，必须以 **DONE** 或 **FAILED** 收尾，否则 Watchdog 会在超时后判定为僵尸任务并自动重试。
- 心跳必须持续，注册后若 30 秒内未收到心跳，Consul 会标记你为 critical，2 分钟后自动注销。**强烈建议**通过后台循环（`while sleep 10; do curl ...; done &`）维持心跳。
- 写产物时使用 **context** 写到需求上下文（跨任务可见），否则只对当前任务可见。API Spec 应该写到 context，PR URL 写到 task。
- 日志记录粒度应聚焦每个**用户可感知的步骤**（如"创建分支"、"实现接口 X"、"通过单元测试"），不要每行代码都记录。

## 集成提示模板

针对常见编码智能体，`templates/` 目录提供了 Prompt 接入模板。`templates/codex_prompt.md` 适用于 Codex CLI；`templates/opencode_prompt.md` 适用于 OpenCode；`templates/claude_code_prompt.md` 适用于 Claude Code。请由调度方在启动 Agent 时将对应模板内容注入到智能体的 System Prompt。

## 故障排查

| 现象 | 可能原因 | 解决 |
| :--- | :--- | :--- |
| CAS 抢占返回 `false` | 任务非 PENDING 或被抢先 | `curl http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/$TASK_NAME/status?raw` 检查状态 |
| curl 返回 HTTP 503 | Consul 不可达 | 确认 dev mode 已启动：`consul members` |
| 反馈一直无响应 | 当前无失败需修复 | 这是正常状态，正常执行业务即可 |
| Consul 连接 503 | Consul 服务异常 | 检查 Consul 进程是否正常运行 |
