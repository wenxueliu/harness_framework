# 作业流模板与实例实现设计

> 版本：v1.1
> 状态：实现设计，待评审后实施
> 更新日期：2026-09-21
> 依据：[产品需求](docs/product-requirements.md)、[用户旅程图](docs/user-journey-map.md)、[系统架构](docs/architecture.md)
> 本文只定义实现方案，不代表代码已经完成

## 1. 实现目标

本系统以以下关系作为唯一产品和执行主线：

```text
Project Group
  → Workflow Template
    → Template Version
      → Workflow Instance
        → Run
          → Task Attempt
            → Execution Manifest
              → ACP Agent Session
```

必须实现：

1. 一个模板版本创建多个相互隔离的实例；
2. 实例独立保存参数、Git 引用、Workspace 和执行策略；
3. 发布 V2 不改变 V1 或已经创建的实例；
4. 模板支持保存草稿、仅发布、发布并创建实例、发布并执行；
5. Task 重试留在原实例，整实例修改和重执行创建 successor instance；
6. Task Attempt 启动前完成能力预检并冻结 Execution Manifest；
7. 通过 ACP Adapter 启动 Agent，注入已授权 MCP，并按适配器能力装配 Skill；
8. 节点详情有来源感知的返回路径，Dashboard 支持 Light / Dark / System；
9. 当前只支持人工 DAG 编排，不实现大模型自动编排；
10. stage-bridge 不再作为执行或兼容路径。

## 2. 当前代码基线与必须先修正的断点

当前仓库已有 Aggregator、ACPDispatcher、Watchdog、RunManager、WorkspaceManager、WebAPI 和 Dashboard 原型，可以复用其状态、事件和 Workspace 能力。但新领域模型与执行链路尚未完全接通：

- `harness_framework/job_flows.py` 已经写入 `jobflows/templates`、`jobflows/instances`；
- `harness_framework/acp_client.py` 当前 MCP 参数仍为空，需改为接受授权 Grant 解析结果；
- `ACPDispatcher` 当前主要扫描旧 `workflows/` 路径，需收敛为从 Instance/Run/Attempt 读取 ready Task；
- 旧 stage-bridge、旧 Worker 路径、旧 `workflows/` 目录和部署开关不应继续作为新模型兼容目标；
- 当前实现不应通过“新旧双写”隐藏问题，迁移完成后新数据只走 `jobflows/` 领域模型。

实现顺序应先统一身份和执行入口，再补 UI；否则 UI 展示的实例状态会与真实 ACP 执行脱节。

## 3. 领域数据设计

### 3.1 WorkflowTemplate

```json
{
  "template_id": "tpl_x",
  "group_id": "group_x",
  "name": "用户服务发布",
  "description": "人工编排的作业流模板",
  "status": "DRAFT",
  "draft_revision": 4,
  "latest_published_version_id": "ver_2",
  "instance_count": 3,
  "created_by": "user_x",
  "created_at": "...",
  "updated_at": "..."
}
```

模板是可编辑容器，不携带某个实例的参数、Git、Workspace 或运行状态。`service_name` 若存在，只是可选业务标签。

### 3.2 TemplateVersion

```json
{
  "version_id": "ver_2",
  "template_id": "tpl_x",
  "version": 2,
  "status": "PUBLISHED",
  "definition_hash": "sha256:...",
  "definition": {
    "parameters": {},
    "tasks": [],
    "edges": []
  },
  "validation": {
    "valid": true,
    "errors": [],
    "warnings": []
  },
  "published_by": "user_x",
  "published_at": "..."
}
```

发布版本完全不可变。模板编辑页保存的是 draft，不直接修改 version。

### 3.3 WorkflowInstance 和 Run

```json
{
  "instance_id": "ins_x",
  "template_id": "tpl_x",
  "version_id": "ver_2",
  "group_id": "group_x",
  "name": "用户服务发布-开发分支",
  "status": "QUEUED",
  "input_values": {"service": "users"},
  "git": {
    "repository": "repo_x",
    "ref": "refs/heads/feature/users",
    "commit_sha": "abc123"
  },
  "workspace": {
    "workspace_id": "ws_x",
    "mode": "isolated"
  },
  "execution_profile_id": "profile_default",
  "source_instance_id": null,
  "changeset_id": null
}
```

Run 保存一次实际执行的开始、结束、状态和事件索引。实例可以在后续重试或变更时产生多个 Run，但一次“整实例变更并重执行”必须产生新的 successor instance，而不是覆盖原 Instance。

### 3.4 Task、Attempt 和 Manifest

Task 定义来自 TemplateVersion；Task Attempt 是一次实际尝试；Manifest 是 Attempt 启动前解析出的冻结快照。

```json
{
  "attempt_id": "att_x",
  "instance_id": "ins_x",
  "run_id": "run_x",
  "task_id": "backend",
  "status": "READY",
  "capability_preflight": {
    "status": "PASSED",
    "checked_at": "...",
    "issues": []
  },
  "execution_manifest_id": "manifest_x"
}
```

Manifest 至少保存：

- Task Capability Requirements 及其版本；
- Execution Profile 及策略 hash；
- Agent Runtime、Adapter、版本和能力协商结果；
- Skill Bundle、来源、版本和校验摘要；
- MCP Server / Grant、允许暴露的工具和权限范围；
- Workspace Binding、Git commit 和 Secret Reference；
- 资源预算、网络策略、完成门禁和解析来源；
- 生成时间、解析器版本和 Manifest hash。

## 4. JSON 到 DAG

JSON 是唯一输入和持久化表达，服务端生成规范化 DAG：

1. 解析任务数组和边；
2. 规范化 Task ID、字段默认值和引用；
3. 校验重复 ID、自依赖、悬空依赖、成环、必填字段和参数引用；
4. 生成节点/边索引、根节点、叶节点和拓扑顺序；
5. 返回字段级、节点级和边级错误；
6. 前端用同一份规范化模型渲染 DAG，不维护第二份图数据。

模板详情页显示发布版本的只读 DAG；模板编辑页显示 draft JSON 和实时 DAG；实例详情页显示运行状态投影，不能把运行状态回写到模板定义。

## 5. 状态和删除规则

### 5.1 模板/版本

```text
Template: DRAFT → PUBLISHED → ARCHIVED
Version:  DRAFT → PUBLISHED → DISABLED → ARCHIVED
```

没有任何实例绑定的模板允许物理删除；一旦绑定过实例，只能归档。禁用/归档版本不能创建新实例，但历史实例继续可查。

### 5.2 实例

```text
QUEUED → RUNNING → DRAINING → SUCCEEDED
                    ├────────→ FAILED
                    ├────────→ ABORTED
                    └────────→ SUPERSEDED

RUNNING ⇄ PAUSED
```

- `QUEUED` 可以删除，表示取消尚未开始的执行；
- `SUCCEEDED`、`FAILED`、`ABORTED`、`SUPERSEDED`、`ARCHIVED` 可以删除；
- `RUNNING`、`PAUSED`、`DRAINING` 不可直接删除，先暂停/中止/排空；
- 删除只影响实例及实例级数据，不影响模板、版本和其他实例；
- 删除前服务端二次检查状态、权限和幂等键，并写审计。

### 5.3 能力状态

能力预检是 Attempt 的前置状态：

```text
CREATED → PREFLIGHTING → PASSED → READY
             ├──────────→ WAITING_FOR_CAPABILITY
             ├──────────→ UNROUTABLE
             └──────────→ CAPABILITY_INVALID
```

阻塞状态必须返回结构化 `reason_code`、责任方、修复建议和最后检查时间。修复后重新预检会生成新检查记录；真正启动时生成新的 Manifest。

## 6. 执行模块设计

### 6.1 Scheduler / Aggregator

- 读取 Instance 的 version snapshot 和 Run；
- 根据 Task 依赖、前置结果和 Instance 状态计算 ready Task；
- 为每个 Attempt 创建 lease；
- 只把带 `instance_id + run_id + task_id + attempt_id` 的任务包交给 Execution Gateway；
- 聚合 Task、Run、Instance 状态，缓存可从 Attempt 重建。

### 6.2 CapabilityResolver

输入：Task Capability Requirements、Instance Context、Project Group Policy、Execution Profile。

输出：

- 可用 Agent Runtime / Adapter；
- Skill Bundle 解析结果；
- MCP Server / Grant 解析结果；
- Workspace Binding 和 Secret Reference；
- 预检问题或完整 Execution Manifest。

CapabilityResolver 不下载或维护所有外部实现，只验证注册元数据、版本、权限、健康检查和适配器声明。

### 6.3 ExecutionGateway

ExecutionGateway 是唯一的 Attempt 启动入口：

1. 检查 lease 和状态前置条件；
2. 调用 CapabilityResolver；
3. 保存 preflight 结果和 Manifest；
4. 准备 Workspace、Skill Bundle 和 Secret Reference；
5. 通过 ACP Adapter 启动 Agent；
6. 将授权 MCP 配置传入 ACP session/new 或 session/load；
7. 发送任务包、接收更新、工具事件和人工消息；
8. 执行完成门禁，写 Attempt 结果和 Evidence；
9. 释放 lease、刷新下游 Task 状态并发布事件。

### 6.4 ACPAdapter

适配器封装不同 Agent Provider 的启动命令和 ACP 能力协商：

- `initialize` 协商 Agent 信息和支持能力；
- `session/new` 或 `session/load` 建立任务 Session；
- 注入已授权 `mcpServers`；
- 按适配器声明装配 Skill Bundle；
- 统一处理 session update、工具调用、终止、超时和错误；
- 向平台返回 provider、runtime、session 和能力证据。

ACP 没有统一 Skill 参数，因此 `SkillInjector` 必须是适配器能力的一部分；没有对应注入方式时预检失败，不得默默丢弃 Skill。

### 6.5 Watchdog / Recovery

Watchdog 以 Attempt lease 和 heartbeat 为依据，不以模板 ID 或 service_name 为运行身份。恢复规则：

- Agent 尚未建立 Session：可重新获取 lease 并启动；
- Session 已建立但连接断开：按适配器能力恢复 Session 或创建新 Attempt；
- MCP/Workspace 失效：进入能力等待，不消耗普通 Task 重试次数；
- 外部副作用已发生：按幂等声明或补偿 Task 处理。

## 7. 存储布局

```text
jobflows/
  templates/{template_id}/meta.json
  templates/{template_id}/draft.json
  templates/{template_id}/versions/{version_id}/definition.json
  templates/{template_id}/versions/{version_id}/validation.json
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

Consul、Local、FileStore 只提供存储实现；对象 ID、revision、幂等和并发条件必须保持同一语义。

## 8. API 设计

所有写接口支持 `Idempotency-Key`；草稿、实例和 ChangeSet 写入支持 `expected_revision`。

### 8.1 Template / Version

```text
GET    /api/templates?group_id=:groupId
POST   /api/templates
GET    /api/templates/:templateId
DELETE /api/templates/:templateId
PATCH  /api/templates/:templateId/draft
POST   /api/templates/:templateId/validate
POST   /api/templates/:templateId/publish
GET    /api/templates/:templateId/versions
GET    /api/templates/:templateId/versions/:versionId
POST   /api/templates/:templateId/versions/:versionId/disable
POST   /api/templates/:templateId/versions/:versionId/archive
```

### 8.2 Instance / Run / Task

```text
GET    /api/instances?group_id=:groupId
POST   /api/instances
GET    /api/instances/:instanceId
DELETE /api/instances/:instanceId
POST   /api/instances/:instanceId/start
POST   /api/instances/:instanceId/pause
POST   /api/instances/:instanceId/abort
POST   /api/instances/:instanceId/rerun
GET    /api/instances/:instanceId/tasks
GET    /api/instances/:instanceId/tasks/:taskId
POST   /api/instances/:instanceId/tasks/:taskId/retry
GET    /api/instances/:instanceId/events
```

`rerun` 创建 successor instance；不能通过 PATCH 原实例的 version、参数或运行上下文来伪装成重跑。

### 8.3 Capability / Manifest

```text
GET    /api/execution-profiles?group_id=:groupId
GET    /api/agent-runtimes
GET    /api/skill-bundles?group_id=:groupId
GET    /api/mcp-servers?group_id=:groupId
POST   /api/instances/:instanceId/preflight
GET    /api/instances/:instanceId/preflight
GET    /api/attempts/:attemptId/execution-manifest
GET    /api/attempts/:attemptId/capability-events
```

首期可只开放查询和预检接口，能力注册/授权管理可以在平台治理阶段实现；但执行接口必须使用同一套解析结果。

### 8.4 ChangeSet

```text
POST   /api/instances/:instanceId/changesets
POST   /api/instances/:instanceId/changesets/:id/analyze
POST   /api/instances/:instanceId/changesets/:id/approve
POST   /api/instances/:instanceId/changesets/:id/apply
POST   /api/instances/:instanceId/changesets/:id/reject
POST   /api/templates/:templateId/changesets
```

不新增旧 `/api/workflows` 语义，不保留 stage-bridge 兼容接口；已有未发布代码中的旧入口应在实现阶段删除或直接迁移为新资源接口。

## 9. 前端实现设计

### 9.1 路由

```text
/templates
/templates/new
/templates/:templateId
/templates/:templateId/edit
/templates/:templateId/versions/:versionId
/templates/:templateId/instances/new
/instances
/instances/:instanceId
/instances/:instanceId/tasks/:taskId
/instances/:instanceId/attempts/:attemptId
/instances/:instanceId/preflight
/instances/:instanceId/manifest
/instances/:instanceId/changesets/:changesetId
```

### 9.2 页面职责

- TemplateList：按 Project Group 查询模板，展示草稿、发布版本和实例数量；
- TemplateDetail：只读元数据、版本、校验结果和 DAG；
- TemplateEditor：编辑草稿 JSON，实时展示 DAG，定位错误和进入发布；
- PublishDialog：明确区分保存草稿、仅发布、发布并创建实例、发布并执行；
- InstanceCreate：选择版本，配置参数、Git、Workspace 和 Execution Profile；
- InstanceDetail：展示实例上下文、能力状态、DAG 运行投影、事件和控制动作；
- TaskDetail/Workbench：展示 Attempt、Session、Manifest、日志、Evidence 和 Workspace；
- ChangeSetPanel：展示影响分析、复用依据、审批、successor 和排空进度。

节点详情返回规则：优先读取 router history 的来源；来自模板编辑页回编辑页，来自模板详情页回详情页，来自实例 DAG 回实例详情；没有来源时回实例详情。

### 9.3 主题

- 支持 `system`、`light`、`dark`，默认跟随系统并持久化；
- 使用语义设计令牌，DAG、节点、编辑器、日志和告警一起切换；
- 状态使用文字/图标/形状与颜色组合表达；
- 主题变化不影响草稿、实例状态和事件订阅。

## 10. 事务与幂等

### 10.1 发布并执行

```text
校验 draft revision
  → 创建/复用 Template Version
  → 创建 Instance
  → 冻结输入/Git/Workspace/Profile
  → 创建 Run
  → 创建首批 Attempt
  → Capability Preflight
  → 通过后进入 QUEUED/RUNNING
```

任一步失败都保留可审计中间状态；重复请求返回已创建资源，不重复发布版本、不重复创建实例。

### 10.2 实例修改并重执行

```text
读取实例快照
  → 创建 ChangeSet
  → 计算影响范围和复用资格
  → 审批
  → 创建 successor instance
  → successor 预检/启动
  → 原实例 DRAINING 或保留历史
```

successor 创建失败时原实例不得被误中止；successor 成功后通过 `source_instance_id`、`changeset_id` 和审计事件建立双向链接。

## 11. 测试与验收

### 11.1 API / 领域测试

- 一个模板版本创建多个实例，参数、Git、Workspace、状态和日志互不污染；
- 发布 V2 后 V1 实例的 definition_hash 不变化；
- 模板无实例可以删除，有实例只能归档；QUEUED 和终态实例可以删除；
- Task retry 新增 Attempt，rerun 新增 successor；
- expected_revision、Idempotency-Key、权限和状态前置条件有效；
- Capability Preflight 对缺失 Skill、MCP、Workspace、Agent Runtime 返回结构化错误；
- Manifest 生成后平台默认配置变化不影响历史 Attempt；
- MCP 未授权工具不会进入 ACP session；Skill 不兼容时不启动 Agent；
- stage-bridge 和旧 `/api/workflows` 不会被新执行链路调用。

### 11.2 UI 自动化

- 模板列表 → 详情 → 编辑 → 保存草稿 → 发布；
- 仅发布、发布并创建实例、发布并执行产生不同资源结果；
- 一个模板版本创建两个参数/Git/Workspace 不同的实例；
- 模板详情 DAG 只读，编辑页 JSON 与 DAG 同步；
- 点击 DAG 节点后按来源返回；
- 能力预检阻塞、修复后重试、Manifest 查看；
- 从运行实例创建 ChangeSet、确认 successor、查看旧实例历史；
- QUEUED、终态和 RUNNING 删除规则；
- Light/Dark/System 切换和刷新保持；
- API 失败、网络断开和 SSE 重连提示清晰。

### 11.3 运行时门禁

在以下条件未满足前不进入正式代码实现：

1. 新领域身份、API 和状态机评审通过；
2. Capability Requirement、Execution Profile、Skill/MCP/Runtime 元数据和 Manifest schema 评审通过；
3. ACP Adapter 的 Skill/MCP 注入能力声明明确；
4. stage-bridge 删除范围明确；
5. 多实例、V1/V2、successor、删除和能力阻塞的测试用例评审通过。
