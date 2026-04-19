---
name: stage-bridge
description: 多 Agent 开发平台的 Consul 状态桥接 Skill。当编码智能体（如 Codex / OpenCode / Claude Code）作为执行层 Agent 加入多 Agent 软件开发流程时，加载此 Skill 以读写 Consul KV、抢占任务、记录日志、传递上下文、上报失败反馈。仅用于已部署 stage-bridge 框架（含 Consul dev mode）的本地开发环境。
---

# stage-bridge — 多 Agent 状态桥接 Skill

## 何时使用

读取本 Skill 当且仅当你被指派为 **多 Agent 开发平台的某种执行层 Agent**（设计 / 评审 / 开发 / 测试 / 部署等），且需要与其他 Agent 通过 Consul 协作。如果你只是单独执行一个独立的编码任务，不需要本 Skill。

判断信号包括：环境变量中存在 `CONSUL_ADDR`、`AGENT_ID`、`REQ_ID`、`TASK_NAME` 中的一个或多个；用户/调度方明确告知你是某需求的某个 Agent；你被要求上报进度、读取上游产物或写入失败反馈。

## 核心概念

| 概念 | 说明 |
| :--- | :--- |
| 需求（req_id） | 一次完整的研发需求，对应一棵 DAG 任务树 |
| 任务（task_name） | DAG 中的一个节点，由某个 Agent 执行 |
| 上下文（context） | 需求级共享数据（API Spec、上游产物等） |
| 会话（session） | 一次任务执行的事件流，含日志、决策、产物 |
| 反馈（feedback） | 测试 Agent 失败后写给具体服务 Agent 的修复请求 |

Consul KV 路径约定：

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

下表是任意类型 Agent 的标准协作流程。所有命令位于 `scripts/` 目录下，均接受命令行参数，输出 JSON 到 stdout。

| 阶段 | 命令 | 调用时机 |
| :--- | :--- | :--- |
| 注册（后台） | `auto_register.py --capabilities <c1,c2> [--service <name>]` | **推荐**：一键后台注册 + 心跳循环 |
| 状态 | `auto_register.py --status` | 查看后台进程状态 |
| 停止 | `auto_register.py --stop` | 停止后台进程 |
| 注册（前台） | `auto_register.py --capabilities <c1,c2> --foreground` | 调试模式，日志输出到 stderr |
| 注册（旧） | `register_agent.py --capabilities <c1,c2>` | 单独注册（需配合 heartbeat.py） |
| 抢占（指定） | `claim_task.py <req_id> <task_name>` | 抢占指定的任务 |
| 抢占（自动） | `claim_next_task.py [--capabilities c1,c2] [--loop] [--max-tasks N]` | 自动查找并抢占下一个可用任务 |
| 列出任务 | `claim_next_task.py --list-only` | 仅查看所有 PENDING 任务 |
| 读上下文 | `read_context.py <req_id> [<key>] [--wait]` | 抢占成功后读取上游产物 |
| 记录日志 | `log_step.py <req_id> "<message>" [--level info]` | 任意关键步骤完成后 |
| 写产物 | `write_artifact.py <req_id> <key> <value> [--scope context]` | 产生需向下游传递的数据 |
| 完成 | `complete_task.py <req_id> <task_name> --meta '{...}'` | 任务成功完成 |
| 完成（待评审） | `complete_task.py ... --await-review --pr-url <url>` | 任务完成但需人工 Code Review |
| 失败 | `fail_task.py <req_id> <task_name> --error "..."` | 不可恢复错误 |
| 写反馈 | `feedback_write.py <req_id> <service> --error "..."` | 测试 Agent 专用 |
| 听反馈 | `feedback_listen.py <req_id> <service>` | 服务 Agent 专用：监听修复请求 |
| 解反馈 | `feedback_resolve.py <req_id> <service> --summary "..."` | 服务 Agent 专用：完成修复 |
| ABORT 检查 | `check_control.py <req_id> [--task <task>]` | LLM 调用前后 / verify 每轮 / feedback 唤醒时必检，收到 ABORT 立即退出（码 7） |
| 注销 | `deregister_agent.py` | Agent 退出前 |

必传环境变量：`AGENT_ID`（全局唯一）、`CONSUL_ADDR`（默认 127.0.0.1:8500）；任务相关命令额外需要 `REQ_ID`、`TASK_NAME`。

## .env 配置文件

除环境变量外，脚本支持从 `.env` 文件读取配置。配置优先级：**命令行参数 > 环境变量 > .env 文件 > 默认值**。

`.env` 文件搜索路径（按顺序）：
1. 当前工作目录：`./.env`
2. skill 目录：`~/.claude/skills/stage-bridge/.env`
3. scripts 父目录：`skills/stage-bridge/.env`
4. 固定目录：`~/.claude/stage-bridge/.env`

示例 `.env` 文件：
```bash
# Consul 连接
CONSUL_ADDR=127.0.0.1:8500
CONSUL_TOKEN=

# Agent 配置
AGENT_ID=my-agent
SERVICE_NAME=user-service
REPO_PATH=/path/to/your/service

# 任务配置（可选）
REQ_ID=req-001
TASK_NAME=implement-api
```

## 错误处理与退出码

所有命令遵循统一约定：退出码 0 表示操作成功；退出码 1 表示业务错误（如 CAS 抢占失败、key 不存在），应视为正常分支按业务逻辑处理；退出码 2 表示系统错误（如 Consul 不可达），应重试 3 次后中止任务。业务错误信息走 stderr，stdout 始终是 JSON（成功时）或为空（失败时）。

## 三类 Agent 的典型工作流

### 开发 Agent（绑定一个微服务仓库）

```bash
# === 启动阶段（推荐：使用 auto_register.py 后台运行） ===
AGENT_ID="my-agent" \
python scripts/auto_register.py \
  --capabilities backend \
  --service user-service \
  --max-concurrent 1 \
  --repo-path "$REPO_PATH"
# 输出 JSON: {"ok": true, "agent_id": "...", "pid": 12345, "mode": "daemon"}
# 进程在后台运行，日志写入 ~/.claude/stage-bridge/<agent_id>.log
# PID 文件: ~/.claude/stage-bridge/<agent_id>.pid

# 查看状态
AGENT_ID="my-agent" python scripts/auto_register.py --status

# 停止后台进程
AGENT_ID="my-agent" python scripts/auto_register.py --stop

# 调试模式（前台运行，日志输出到 stderr）
AGENT_ID="my-agent" python scripts/auto_register.py --capabilities backend --foreground

# === 或者分步执行（旧方式） ===
# python scripts/register_agent.py \
#   --capabilities backend \
#   --service user-service \
#   --max-concurrent 1 \
#   --repo-path "$REPO_PATH"
# python scripts/heartbeat.py --loop 10 &

# === 任务执行阶段（两种模式）===

## 模式 A：抢占指定任务
python scripts/claim_task.py "$REQ_ID" "$TASK_NAME"

## 模式 B：自动依次执行多个任务（推荐）===
python scripts/claim_next_task.py --capabilities backend,translate --loop

# Agent 会自动：
# 1. 查找所有 PENDING 任务
# 2. 过滤匹配 capabilities 和 service_name 的任务
# 3. 按优先级抢占（依赖少的先执行）
# 4. 执行完成后再次抢占下一个任务
# 5. 直到没有可用任务

# === 单个任务执行流程 ===
python scripts/read_context.py "$REQ_ID" api_spec_url --wait

# 在 $REPO_PATH 下执行实际编码工作（智能体核心能力）：
# 创建 feature 分支 → 修改/新增代码 → 运行单元测试 → 提交 push 创建 PR

python scripts/log_step.py "$REQ_ID" "Implemented user authentication module"

python scripts/write_artifact.py "$REQ_ID" pr_url \
  "https://gitlab.example.com/.../merge_requests/42"

python scripts/complete_task.py "$REQ_ID" "$TASK_NAME" \
  --await-review \
  --pr-url "https://gitlab.example.com/.../merge_requests/42" \
  --meta '{"branch":"feature/req-001-auth","commit":"a1b2c3d"}'

# === 抢占下一个任务 ===
python scripts/claim_next_task.py --capabilities backend
```

### 测试 Agent（无服务绑定）

```bash
python scripts/register_agent.py --capabilities test --max-concurrent 1
python scripts/heartbeat.py --loop 10 &

python scripts/claim_task.py "$REQ_ID" "test-e2e"
python scripts/read_context.py "$REQ_ID"

# 执行测试（智能体核心工作）...

# 失败时归因到具体服务
python scripts/feedback_write.py "$REQ_ID" "user-service" \
  --error "Login API returns 500" \
  --failed-cases-file ./failed_cases.json \
  --severity high

python scripts/fail_task.py "$REQ_ID" "test-e2e" \
  --error "2 services failed: user-service, order-service" \
  --retry-hint retry
```

### 服务 Agent 接收反馈（开发 Agent 的"修复模式"）

```bash
FEEDBACK=$(python scripts/feedback_listen.py "$REQ_ID" "user-service" --timeout 600)
# FEEDBACK 是 JSON，含 payload.error_summary, payload.failed_cases

# 智能体根据反馈自主诊断 + 修复 + 提交...

python scripts/feedback_resolve.py "$REQ_ID" "user-service" \
  --summary "修复了 /api/login 的 NPE 异常" \
  --commit "$(git rev-parse HEAD)"
```

框架的 Aggregator 会自动检测：当所有 service 的反馈状态都是 FIXED 时，会清除反馈并将测试任务重置为 PENDING，触发重新测试。

## 重要约束

任务一旦被 `claim_task.py` 抢占成功，必须以 `complete_task.py` 或 `fail_task.py` 之一收尾，否则 Watchdog 会在超时后判定为僵尸任务并自动重试。

心跳必须持续，注册后若 30 秒内未收到心跳，Consul 会标记你为 critical，2 分钟后自动注销，**强烈建议**通过 `heartbeat.py --loop 10 &` 启动后台心跳进程。写产物时使用 `--scope context` 跨任务共享，否则只对当前任务可见，例如 API Spec 应该写到 context，PR URL 写到 task。日志记录粒度应聚焦每个**用户可感知的步骤**（如"创建分支"、"实现接口 X"、"通过单元测试"），不要每行代码都记录。

## 集成提示模板

针对常见编码智能体，`templates/` 目录提供了 Prompt 接入模板。`templates/codex_prompt.md` 适用于 Codex CLI；`templates/opencode_prompt.md` 适用于 OpenCode；`templates/claude_code_prompt.md` 适用于 Claude Code。请由调度方在启动 Agent 时将对应模板内容注入到智能体的 System Prompt。

## 故障排查

| 现象 | 可能原因 | 解决 |
| :--- | :--- | :--- |
| `claim_task.py` 总是 exit 1 | 任务非 PENDING 或被抢先 | `curl http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/$TASK_NAME/status?raw` 检查状态 |
| `register_agent.py` / `auto_register.py` exit 2 (HTTP 503) | urllib 代理问题 | 使用 `auto_register.py`（已修复）或确保无代理设置 |
| `register_agent.py` / `auto_register.py` exit 2 | Consul 不可达 | 确认 dev mode 已启动：`consul members` |
| 心跳报 404 | 服务已被自动注销 | 重新执行 `auto_register.py` 或 `register_agent.py` |
| `feedback_listen.py` 一直阻塞 | 当前无失败需修复 | 这是正常状态，按 Ctrl+C 退出或等待超时 |
| Consul 连接 503 但 curl 正常 | urllib 默认代理设置 | 使用 `auto_register.py`（内置禁用代理） |
