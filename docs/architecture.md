# Harness Orchestration 系统架构

> 版本：v1.1
> 状态：目标架构与当前实现差距说明
> 更新日期：2026-09-21
> 相关文档：[产品需求](product-requirements.md)、[用户旅程图](user-journey-map.md)、[实现设计](../DESIGN.md)、[ACP 执行](acp-execution.md)

## 1. 架构结论

Harness Orchestration 的核心不是一个“启动 Agent 的脚本”，而是一个以模板版本和实例为事实源、以 ACP 为执行协议、以能力预检和 Execution Manifest 保证可复现的作业流控制平面。

目标架构分为五个平面：

```text
┌──────────────────────────────────────────────────────────┐
│ Product Control Plane                                    │
│ Project Group / Template / Version / Instance / ChangeSet│
└──────────────────────────┬───────────────────────────────┘
                           │ normalized graph + policy
┌──────────────────────────▼───────────────────────────────┐
│ Scheduler Plane                                           │
│ DAG validation / readiness / leases / Run & Task state    │
└───────────────┬──────────────────────┬────────────────────┘
                │ attempt package      │ events / audit
┌───────────────▼──────────────┐  ┌────▼────────────────────┐
│ Capability Plane              │  │ Observation Plane        │
│ Profile / Skill / MCP /       │  │ event log / audit / SSE  │
│ Runtime / Workspace / Secret │  │ evidence / artifact      │
└───────────────┬──────────────┘  └─────────────────────────┘
                │ Execution Manifest
┌───────────────▼───────────────────────────────────────────┐
│ Execution Plane                                            │
│ Execution Gateway → ACP Adapter → Agent Runtime / Session  │
└─────────────────────────────────────────────────────────────┘
```

stage-bridge 不属于目标架构。项目尚未发布，不保留兼容执行路径；原有 stage-bridge 的上下文、产物、证据和状态反馈职责由 Control Plane、Execution Gateway 和 Observation Plane 重新承载。

## 2. 领域事实源

```text
Project Group
  └── Workflow Template
        └── Template Version（不可变）
              └── Workflow Instance（绑定一个版本）
                    └── Run
                          └── Task Attempt
                                ├── Agent Session
                                ├── Execution Manifest
                                ├── Evidence / Artifact
                                └── Audit Events
```

### 2.1 不可变边界

- Template 是可编辑容器；草稿变化不改变已经发布的 Version。
- Template Version 是发布快照；V2 只能生成新版本，不覆盖 V1。
- Instance 快照保存参数、Git、Workspace、执行策略和来源版本。
- Run 记录一次实例执行；Task Attempt 记录一次真实尝试。
- Task 重试只创建新 Attempt；整实例修改并重执行创建 successor instance。
- Execution Manifest 在 Attempt 启动前生成并冻结，平台默认配置变化不能回写历史 Attempt。

### 2.2 Project Group 边界

Project Group 是模板、实例、成员、Workspace 和执行策略的授权边界。实例不能跨组移动；模板的 `service_name` 只是可选业务标签，不参与 Agent 路由、Workspace 解析或权限判断。

## 3. 平面职责

### 3.1 Product Control Plane

负责：

- Template、Template Version、Instance、Run、Task、ChangeSet 的 CRUD 和状态转换；
- JSON 规范化为唯一 DAG 模型；
- 发布、归档、删除、实例创建、变更重执行的权限、幂等和审计；
- 根据用户旅程向 UI 提供模板详情、编辑、实例和节点详情数据。

不负责：

- 直接实现 Agent 的推理逻辑；
- 让模板直接绑定一个长期驻留的 Agent 进程；
- 把外部 Skill 或 MCP 的全部实现代码复制到平台。

### 3.2 Scheduler Plane

负责：

- 根据发布版本生成可执行 DAG；
- 按依赖判断 Task 是否 ready；
- 为 Task Attempt 创建 lease，避免重复执行；
- 聚合 Task、Attempt 和 Run 状态；
- 处理暂停、排空、中止、重试、跳过和失败传播；
- 将 ready 的 Attempt 交给 Execution Gateway。

Scheduler 只决定“哪个 Attempt 现在允许启动”，不决定 Agent 是否具备执行能力。能力判断由 Capability Plane 完成。

### 3.3 Capability Plane

Capability Plane 维护可引用的元数据、版本、权限、健康检查和适配器，不集中维护所有实现：

| 对象 | 作用 | 典型所有者 |
|---|---|---|
| Task Capability Requirements | Task 需要的抽象能力、资源、风险和完成门禁 | 模板作者 |
| Execution Profile | 允许的运行时、Workspace、网络、预算和秘密策略 | 平台/项目组 |
| Skill Bundle | 版本化指令、规则和辅助资源 | 平台、项目组或外部仓库 |
| MCP Registry Entry | MCP 服务地址、版本、健康检查和工具元数据 | 平台/服务所有者 |
| MCP Grant | 本次执行可暴露、可调用的 MCP 权限 | 项目组/平台策略 |
| Agent Runtime | ACP Agent 的启动方式、版本和能力声明 | 平台适配器/Agent 提供方 |

平台维护“引用和治理”，而不是维护一个包含全部 Skill、MCP、Agent 实现的中央仓库。外部实现必须提供可验证的版本、入口、能力声明和健康结果。

### 3.4 Execution Plane

执行链路如下：

```text
Scheduler
  → Execution Gateway
    → Resolve Capability Requirements
    → Run Capability Preflight
    → Build Execution Manifest
    → Prepare Workspace / Secret References / Skill Bundle
    → ACP Adapter.initialize()
    → ACP Adapter.session/new(mcpServers=authorized grants)
    → prompt + task package
    → Agent updates / tool calls / session events
    → Completion Gate + evidence validation
    → Attempt / Task / Run status commit
```

Execution Gateway 是唯一的 Attempt 启动入口。它负责装配任务包、保存 Manifest、传递 ACP 参数、接收 Agent 事件并将结果写回控制平面；Scheduler 和 UI 不应绕过它直接拉起 Agent。

### 3.5 Observation Plane

所有状态变化和重要执行事件进入统一事件流：

- Task、Attempt、Run 和 Instance 状态迁移；
- ACP Session 更新、工具调用、Agent 消息和人工消息；
- Capability Preflight 结果和 Manifest 摘要；
- Workspace 绑定、文件变更、Evidence、Artifact；
- 发布、删除、暂停、中止、重试、变更和权限拒绝。

UI 通过 HTTP 获取快照、通过 SSE 订阅增量事件。事件带有 `instance_id`、`run_id`、`task_id`、`attempt_id` 和单调事件 ID，保证不同实例的日志和状态不会串联。

## 4. ACP、Skill、MCP 和 Agent 的装配原则

### 4.1 ACP 负责什么

ACP 是 Harness 与具体 Agent Runtime 之间的会话协议，负责初始化、能力协商、创建会话、提示和事件流。ACP 不定义一个通用的 Skill 注册协议，也不自动替平台完成权限治理。

因此：

- Agent Runtime 通过 ACP Adapter 接入；
- MCP 配置在 ACP 支持的会话初始化/创建参数中注入；
- Skill Bundle 由适配器根据 Agent 的支持方式注入工作区规则、项目配置或 provider-specific 参数；
- 适配器必须声明支持哪些注入方式，Capability Preflight 负责拒绝不兼容组合；
- 任何“启动了 Agent”但没有保存 Manifest 的执行，都不是可审计的正式 Attempt。

### 4.2 MCP 的两层控制

MCP Server 注册不等于执行可以调用。至少区分：

1. Exposure：该 MCP Server/工具是否可以进入 Agent Session 的可见工具列表；
2. Invocation：当前 Task Attempt 是否有权限实际调用该工具。

Grant 需要包含来源、作用域、版本、过期策略、敏感参数引用和审计要求。Agent 的工具调用事件必须记录工具名、Attempt、结果摘要和拒绝原因，但不得把秘密写入日志。

### 4.3 Skill 的供应方式

Skill 不作为 Task 的任意长文本 prompt 直接拼接。推荐优先级：

1. 项目组/平台注册的版本化 Skill Bundle；
2. Agent Runtime 支持的项目规则或工作区 Skill 目录；
3. provider-specific 的启动参数或配置文件；
4. 只有在适配器明确支持时，才允许使用会话级文本指令。

每个 Skill Bundle 需要有版本、来源、校验摘要、适用 Agent Runtime 和撤销状态。Skill 读取失败应阻塞 Attempt，而不是降级为无 Skill 执行。

## 5. 一次 Attempt 的时序

```text
用户启动实例
  ↓
Control Plane 创建/确认 Run
  ↓
Scheduler 找到 ready Task
  ↓
创建 Attempt + lease
  ↓
Execution Gateway 解析能力需求
  ↓
Capability Preflight
  ├─失败 → WAITING_FOR_CAPABILITY / UNROUTABLE + 修复建议
  └─通过
       ↓
  生成并冻结 Execution Manifest
       ↓
  绑定 Workspace、准备 Skill、解析 Secret Reference
       ↓
  ACP initialize / session-new
       ↓
  注入已授权 MCP Servers / task package
       ↓
  Agent 执行并发出 Session Update
       ↓
  Completion Gate 校验输出、证据和副作用声明
  ├─失败 → FAILED / 可重试原因
  └─通过 → SUCCEEDED
       ↓
  Scheduler 重新计算下游依赖
```

预检和 Manifest 必须在 Attempt 范围内，而不是只在模板发布时做一次。因为同一模板版本的不同实例可以拥有不同参数、Git、Workspace 和执行权限。

## 6. 状态与错误语义

### 6.1 实例状态

```text
QUEUED → RUNNING → DRAINING → SUCCEEDED
                    ├────────→ FAILED
                    ├────────→ ABORTED
                    └────────→ SUPERSEDED

RUNNING ⇄ PAUSED
```

能力预检状态作为实例/Attempt 的可见阻塞原因，推荐使用：

- `WAITING_FOR_CAPABILITY`：缺少 Skill、MCP、Workspace、权限或资源；
- `UNROUTABLE`：没有满足要求的 Agent Runtime 或适配器；
- `CAPABILITY_INVALID`：版本、schema 或策略不兼容。

这些状态都必须带结构化原因、责任方、修复动作和最近一次预检时间。

### 6.2 恢复策略

- ACP 进程未启动：可重试，不生成成功 Attempt；
- ACP 会话中断：按 Agent Runtime 能力选择恢复 Session 或创建新 Attempt；
- MCP 健康检查失败：等待修复，不应循环消耗 Task 重试次数；
- Workspace 冲突：要求用户选择新 Workspace 或显式确认安全边界；
- 完成门禁失败：保留日志、证据和 Manifest，进入失败或人工处理；
- 外部副作用已发生：必须根据幂等声明或补偿任务处理，不得盲目重跑。

## 7. 数据与存储布局

目标存储以新领域身份为主：

```text
jobflows/
  templates/{template_id}/meta.json
  templates/{template_id}/draft.json
  templates/{template_id}/versions/{version_id}/definition.json
  instances/{instance_id}/meta.json
  instances/{instance_id}/runs/{run_id}/meta.json
  instances/{instance_id}/runs/{run_id}/tasks/{task_id}/attempts/{attempt_id}/meta.json
  instances/{instance_id}/runs/{run_id}/tasks/{task_id}/attempts/{attempt_id}/manifest.json
  instances/{instance_id}/runs/{run_id}/events/{event_id}.json
  changesets/{changeset_id}.json

capabilities/
  execution-profiles/{profile_id}/{version}.json
  skill-bundles/{bundle_id}/{version}.json
  mcp-servers/{server_id}/{version}.json
  agent-runtimes/{runtime_id}/{version}.json
  grants/{grant_id}.json
```

实现可以使用 Consul、Local 或 FileStore，但必须保持对象 ID、revision、幂等键和并发条件语义一致。Manifest 是 Attempt 的历史快照，不得只存一份当前解析配置。

## 8. 当前代码差距与收敛要求

当前仓库存在一个必须优先收敛的断点：新的 `job_flows.py` 已经在 `jobflows/templates` 和 `jobflows/instances` 写入模板/实例数据，但 `ACPDispatcher` 的待处理任务扫描仍主要面向旧的 `workflows/` 路径。目标架构要求：

1. Template/Version/Instance/Run/Task Attempt 成为唯一执行身份；
2. Scheduler 从 Instance 的发布版本和 Run 状态计算 ready Task；
3. Execution Gateway 只接受带 Instance/Run/Attempt 身份的任务包；
4. ACPDispatcher 演进为 ACP Adapter/Execution Gateway 的实现，不再扫描旧 Workflow 目录；
5. 删除 stage-bridge、旧 Worker 兼容路径及依赖它们的部署开关；
6. 通过 API、UI 自动化和运行时测试验证多个实例不会串状态。

## 9. 安全与审计

- Project Group、Template、Version、Instance、Workspace 和 Capability Grant 均经过授权检查；
- Secret 只以 Reference 进入 Manifest 和 Agent 配置，明文不进入模板、日志、Prompt 或事件；
- MCP 工具按 exposure/invocation 双层授权，拒绝事件可追踪；
- Agent Runtime 只能访问 Attempt 绑定的 Workspace 和受控工具；
- 所有高风险操作带 actor、原因、前置状态、幂等键和结果；
- 外部副作用 Task 必须声明幂等范围或补偿任务；
- 日志和事件按 Instance/Run/Attempt 分区，默认脱敏并限制下载权限。

## 10. 可观测性

至少记录以下指标和关联字段：

| 类别 | 指标/字段 |
|---|---|
| 调度 | ready 延迟、lease 冲突、队列等待、依赖阻塞 |
| 能力 | preflight 成功率、缺失能力类型、MCP 健康、Skill 加载耗时 |
| ACP | initialize/session 成功率、Agent 启动耗时、Session 中断、工具拒绝 |
| 执行 | Attempt 时长、重试率、完成门禁失败率、Workspace 冲突 |
| 产品 | 草稿保存、发布、实例创建、successor、删除和审计失败 |

所有指标必须关联 `project_group_id`、`template_id`、`version_id`、`instance_id`、`run_id` 和 `attempt_id`，便于从 UI 事件跳转到完整上下文。

## 11. 实施阶段

### Phase 0：领域和执行契约收敛

- 固化 Template/Version/Instance/Run/Task Attempt 数据模型；
- 统一 JSON→DAG 规范化和状态机；
- 明确 Task Capability Requirements、Execution Profile 和 Manifest schema；
- 清理 stage-bridge 和旧 Workflow/Worker 兼容入口。

### Phase 1：ACP Execution Gateway

- 将现有 ACP 调度收敛为带 Instance/Run/Attempt 身份的 Gateway；
- 增加 Agent Runtime/Adapter 注册和 ACP 能力协商；
- 接通 Workspace、Task package、完成门禁和事件写回；
- 补齐 Attempt lease、幂等和崩溃恢复。

### Phase 2：Capability Plane

- Execution Profile、Skill Bundle、MCP Registry/Grant 元数据；
- Capability Preflight、修复建议和 `WAITING_FOR_CAPABILITY`；
- ACP MCP 注入和 Skill provider-specific 注入；
- Manifest 查询、审计和重试差异。

### Phase 3：产品 UI 和用户旅程

- 模板列表/详情/编辑、JSON+DAG 预览、发布三动作；
- 实例创建、多个实例隔离、实例详情和节点返回；
- Light/Dark 主题、能力状态和 Manifest 展示；
- 删除、暂停、重试、变更重执行和 successor。

### Phase 4：验证与平台化

- API 契约测试、UI 自动化、端到端多实例测试；
- 安全、权限、MCP 脱敏、Workspace 边界和副作用补偿；
- 资源预算、能力健康度、审批和后续触发器。

## 12. 架构验收标准

1. 新建作业流从 Template Version 到 Instance/Run/Attempt 全链路使用统一身份。
2. 同一版本的多个实例可以拥有不同参数、Git、Workspace 和 Execution Profile。
3. 每个 Attempt 启动前都有 Capability Preflight 和不可变 Execution Manifest。
4. ACP Agent 只获得授权的 Skill 和 MCP；不满足适配器能力声明时不启动。
5. stage-bridge 不再出现在生产执行路径、部署开关和产品文档的兼容说明中。
6. V2 发布不会改变 V1 实例；实例变更通过 successor 保留完整历史。
7. 模板、实例、Task、Attempt、Session、日志和证据在 UI 中可按上下文互相跳转。
8. API、UI 自动化和关键运行时测试覆盖发布、能力预检、多个实例、删除和重执行。

## 13. 外部架构参考

本设计吸收了以下公开项目的可复用思想，但不直接复制其领域模型：

- [Multica](https://deepwiki.com/multica-ai/multica)：任务级 Agent 配置、隔离执行环境、按任务装配 Skill/MCP 和中心调度；
- [Paperclip](https://deepwiki.com/paperclipai/paperclip)：控制平面与执行目标分离、工具暴露/调用权限治理、Skill Catalog 和安全拒绝；
- [Vibe Kanban](https://deepwiki.com/BloopAI/vibe-kanban)：任务尝试级 Workspace、Session/Process 生命周期、重试/重置和实时事件。

结合本项目约束后的取舍是：不维护一个包含全部实现的 Capability Profile 大目录，而是维护可验证的引用、版本、策略、Grant 和 Adapter；每个 Attempt 在运行前解析为自己的 Execution Manifest。
