# ACP Agent 执行设计

> 版本：v1.1
> 更新日期：2026-09-21
> 状态：目标执行链路
> 相关文档：[系统架构](architecture.md)、[实现设计](../DESIGN.md)

Harness Orchestration 使用 [Agent Client Protocol](https://agentclientprotocol.com/) 连接受控的 Agent Runtime。ACP 是 Agent 会话协议，不是模板、实例、能力注册或权限治理系统；这些职责由 Harness 的 Control Plane、Capability Plane 和 Execution Gateway 承担。

## 1. 执行链路

```text
Scheduler
  ↓ ready Task Attempt
Execution Gateway
  ↓ Capability Preflight
  ↓ Execution Manifest（冻结）
Workspace / Skill / Secret Reference
  ↓
ACP Adapter
  ├─ initialize（Agent 信息与能力协商）
  ├─ session/new 或 session/load
  ├─ 注入已授权 MCP Servers
  ├─ 按适配器能力装配 Skill Bundle
  └─ session/prompt（Task Package）
       ↓ session/update / tool call / human message
Completion Gate + Evidence
  ↓
Attempt / Task / Run / Instance 状态和事件
```

Execution Gateway 是唯一的 Attempt 启动入口。UI、Scheduler 和其他后台组件都不能绕过 Gateway 直接启动 Agent。

## 2. 一次 Attempt 的身份

所有 ACP 请求都必须携带：

```text
project_group_id
template_id
version_id
instance_id
run_id
task_id
attempt_id
execution_manifest_id
```

同一模板版本的多个实例可以使用不同的参数、Git ref、Workspace、Execution Profile、Skill 和 MCP Grant。不能使用模板 ID 或 `service_name` 作为运行身份。

## 3. Capability Preflight

Attempt 启动前，CapabilityResolver 解析并检查：

- Task Capability Requirements；
- Project Group 权限和 Execution Profile；
- Agent Runtime / ACP Adapter 版本和启动能力；
- Skill Bundle 来源、版本、校验摘要和注入方式；
- MCP Server 健康状态、Grant、可暴露工具和调用权限；
- Workspace Binding、Git commit、资源预算和 Secret Reference；
- 完成门禁、网络策略和外部副作用约束。

结果：

```text
PREFLIGHTING → PASSED → READY
                 ├────→ WAITING_FOR_CAPABILITY
                 ├────→ UNROUTABLE
                 └────→ CAPABILITY_INVALID
```

阻塞结果必须包含 `reason_code`、责任方、修复动作和检查时间。不能把能力缺失显示为普通 `PENDING` 或消耗普通 Task 重试次数。

## 4. Execution Manifest

通过预检后，Gateway 生成并保存 Attempt 级 Manifest，至少包含：

- Agent Runtime、Adapter、版本和 ACP 协商结果；
- Execution Profile 和策略 hash；
- Skill Bundle 的来源、版本、校验摘要和注入方式；
- MCP Server/Grant、允许暴露和调用的工具；
- Workspace Binding、Git commit 和 Secret Reference；
- 资源预算、超时、网络、权限和完成门禁；
- 解析器版本、来源决定和 Manifest hash。

Manifest 只读。平台默认配置变化不能修改历史 Attempt；重试或重新执行需要新 Attempt 和新 Manifest，并展示差异。

## 5. Agent Runtime 和 ACP Adapter

Agent Runtime 是具体 Agent 的执行选项，Adapter 封装启动方式和协议差异。Adapter 必须声明：

- 启动命令和版本探测方式；
- 支持的 ACP 版本和方法；
- 支持 `session/new`、`session/load`、取消和恢复的能力；
- 支持的 MCP 注入方式；
- 支持的 Skill 注入方式；
- 权限、Workspace、超时和资源约束；
- 错误、心跳、Session 恢复和完成原因的映射。

ACP 不提供统一的 Skill 参数。Skill 必须通过以下一种由 Adapter 声明支持的方式装配：

1. Agent 工作区或项目规则目录；
2. Provider-specific 配置文件或启动参数；
3. 适配器支持的会话级指令。

没有支持的注入方式时，预检失败，不静默丢弃 Skill。

## 6. MCP 注入与权限

MCP 需要区分两件事：

1. Exposure：MCP Server/工具是否进入 Agent Session 的可见列表；
2. Invocation：当前 Attempt 是否允许实际调用该工具。

通过预检的 Grant 在 ACP 会话创建时传入授权后的 `mcpServers` 配置。Agent 的工具调用事件必须写入 Attempt 事件流；拒绝调用也要记录原因。MCP 凭据只以 Secret Reference 传递，明文不能进入 Prompt、日志或 Manifest 展示文本。

当前代码中的 `mcpServers: []` 只能视为待实现缺口；正式链路需要由 Grant Resolver 生成授权列表，不能由前端直接提交任意 MCP 配置。

## 7. Task Package 和完成门禁

Prompt/Task Package 包含：

- Task 描述和依赖完成摘要；
- 实例参数的非敏感部分；
- Git、Workspace Binding 和文件访问范围；
- Execution Manifest 的可展示摘要；
- Completion Contract、资源预算和副作用约束；
- 需要交付的 Artifact/Evidence 引用方式；
- 人工消息和恢复上下文。

Agent 结束后，Completion Gate 校验：

- ACP stop reason 是否允许完成；
- 必需 Artifact 是否存在且可读；
- 测试、审查或其他 required gates 是否通过；
- 外部副作用是否符合幂等/补偿声明；
- Agent 是否被拒绝、取消、超时或达到预算。

只有满足完成门禁才将 Attempt 标记为成功；否则保留事件、日志、Manifest 和失败原因。

## 8. 取消、重试和恢复

- 用户取消：Gateway 向 ACP Session 发送 cancel，必要时终止子进程；
- Agent 启动失败：可以重试启动，但不覆盖旧 Attempt；
- Session 断开：如果 Adapter 支持 load，则恢复原 Session，否则创建新 Attempt；
- MCP/Skill/Workspace 失效：进入能力等待，不作为普通业务失败重复消耗次数；
- Task 重试：新建 Attempt，保留旧 Session/日志；
- 实例修改并重执行：创建 successor instance，旧实例保留并按策略排空；
- 外部副作用已经发生：根据幂等声明或补偿 Task 决定是否允许重试。

## 9. 配置示例

任务定义只声明能力和执行偏好，不直接把所有运行细节塞进 Task：

```json
{
  "type": "backend",
  "depends_on": ["design"],
  "description": "实现 /api/users 端点",
  "capability_requirements": {
    "skills": ["python-service@^2"],
    "mcp": ["repo.read", "test.run"],
    "workspace": {"write": true},
    "completion_gates": ["unit-tests"]
  },
  "agent_preference": {
    "runtime": "codex",
    "session_mode": "new"
  },
  "resource_budget": {
    "max_tokens": 100000,
    "max_tool_calls": 200
  }
}
```

Agent Runtime、Skill、MCP 和 Workspace 的最终选择以 Execution Manifest 为准；任务中的 preference 不能绕过项目组策略。

## 10. 运行配置

适配器命令由受控服务配置提供，不能由模板或浏览器传入任意 shell 字符串。示例：

```bash
python -m harness_framework.daemon --local \
  --acp-workspace-root /absolute/path/to/repository \
  --acp-max-concurrency 4 \
  --acp-task-timeout 7200
```

| 配置 | 说明 |
|---|---|
| `ACP_CLAUDE_COMMAND` | Claude ACP Adapter 的固定 argv 配置 |
| `ACP_CODEX_COMMAND` | Codex ACP Adapter 的固定 argv 配置 |
| `ACP_MAX_CONCURRENCY` | Gateway 最大 Attempt 并发 |
| `ACP_TASK_TIMEOUT` | Attempt 的硬超时 |
| `ACP_PERMISSION_POLICY` | Agent 权限默认策略 |

不再提供 `--no-acp-dispatcher` 作为旧 Worker 回退开关。若 Adapter 不可用，Attempt 进入可诊断的 `UNROUTABLE`，而不是切换 stage-bridge。

## 11. 观测和测试

必须记录并可按 Attempt 查询：

- initialize/session 建立耗时和结果；
- Agent Runtime、Adapter、Session ID；
- preflight 结果和 Manifest hash；
- MCP exposure/invocation 和拒绝事件；
- Skill 加载结果；
- session update、工具调用、人工消息、错误和完成原因；
- Artifact、Evidence 和 Completion Gate 结果。

测试至少覆盖：多实例并行隔离、V1/V2、能力缺失、MCP 未授权、Skill 注入失败、Workspace 冲突、Session 恢复、Task 重试、successor 和外部副作用补偿。

## 12. 参考

- [ACP Session Setup](https://agentclientprotocol.com/protocol/v1/session-setup)
- [ACP Initialization](https://agentclientprotocol.com/protocol/v1/initialization)
- [ACP Session Config Options](https://agentclientprotocol.com/protocol/v1/session-config-options)
- [ACP Extensibility](https://agentclientprotocol.com/protocol/v1/extensibility)
