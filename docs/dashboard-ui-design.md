# Harness Dashboard 页面设计

> 版本：v1.1
> 状态：当前产品 UI 设计基线
> 更新日期：2026-09-21
> 参考：`ui/stitch_dag/dag`、`ui/stitch_dag/ai`、`ui/stitch_dag/kinetic_obsidian/DESIGN.md`
> 技术实施计划：[`dashboard-implementation-plan.md`](dashboard-implementation-plan.md)

## 1. 目标与范围

Dashboard 将从单页运行看板升级为面向作业流模板、作业流实例和 ACP Agent 执行的高密度操作台，覆盖：

1. 用项目组组织工作流、成员和默认工作区；
2. 在可交互 DAG 画布中观察和控制工作流；
3. 进入任意 Task 的节点协作工作台，与 Agent 连续交互；
4. 浏览 Agent 当前工作目录中的文件并直接编辑代码；
5. 查看 Task Attempt、Agent Session、运行事件、错误、证据和产物；
6. 在桌面与移动端保持一致的状态语义和操作安全边界。

本文同时描述目标架构和当前里程碑。目标部署环境是内网服务器上的可信团队；本地模式是简化部署，不以多租户 SaaS 为首期目标。尚无后端能力的内容必须显示为不可用状态；样例数据只能在显式 Demo 模式出现，并持续标记为模拟数据。

产品界面统一使用“作业流模板 / 模板版本 / 作业流实例 / 任务节点”，API 和诊断信息使用 `WorkflowTemplate / TemplateVersion / WorkflowInstance / Task`。Pipeline 不是新的领域对象，“节点”只表示 Task 在 DAG 中的视觉形态。

### 1.1 目标架构与当前里程碑

| 范围 | 定义 |
|---|---|
| 目标架构 | 本文第 3–13 节描述的项目组、Run Workspace、节点协作、文件树、编辑器、日志和安全模型 |
| 当前里程碑 | Phase 1：重构真实运行看板、修复状态模型、建立页面框架和 capability 禁用态 |
| 后续里程碑 | Phase 2–6：按后端能力逐步开放项目组、Workspace、编辑和实时能力 |

当前里程碑不使用 Mock 补齐后续功能，也不把 WorkflowBuilder 的本地状态描述为真实发布结果。

## 2. 设计原则

### 2.1 空间优先

DAG、对话和代码是主要工作面，不使用大面积统计卡片挤压可操作空间。概览信息压缩到顶栏、状态条和浮动控件。

### 2.2 一个执行事实源

Workflow、Task、Attempt、Session 和文件状态均由 Harness WebAPI 提供。前端不直连 Consul，也不独立推导与后端冲突的运行状态。

### 2.3 工作区必须与 Agent 一致

文件树和代码编辑器通过当前 Task Attempt 的 `Attempt Workspace Binding` 访问代码。该绑定可以指向共享的 Run Workspace，也可以指向隔离 Workspace；Agent、编辑器和执行历史必须使用同一个绑定。

### 2.4 操作可审计

暂停、中止、重试、interrupt、文件写入和发布均记录 actor、timestamp、目标版本及原因。危险操作必须二次确认。

### 2.5 高密度但不隐藏状态

采用精细边框、紧凑间距、等宽遥测信息和有限状态光效。状态同时使用颜色、图标和文字，不能只依赖颜色。

## 3. 核心领域对象

### Project Group

面向人的所有权与授权边界，包含 Workflow Template、Workflow Instance、成员、Project Workspace 和默认执行策略。项目组可以提供默认 Workspace、Execution Profile 和权限，但不参与 Task 的 Agent 路由。

建议字段：

| 字段 | 含义 |
|---|---|
| `group_id` | 稳定标识 |
| `name` / `description` | 名称与说明 |
| `owner` / `members` | 负责人、成员及角色 |
| `default_workspace_id` | 默认项目工作区 |
| `workflow_count` | 工作流数量 |
| `status` | active / archived |
| `created_at` / `updated_at` | 审计时间 |

一个 Workflow Template 有且只有一个主 Project Group。Workflow Instance 继承该项目组且不能跨组移动；权限、Workspace 和默认策略始终取自主项目组，不在当前版本引入跨组模板引用。

项目组使用 Owner、Maintainer、Developer、Viewer 四级角色：

| 角色 | 默认能力 |
|---|---|
| Owner | 成员、Workspace、项目组策略与归档 |
| Maintainer | 发布、运行、重试、interrupt、暂停、恢复和中止 |
| Developer | 草稿编辑、代码编辑和 queue 消息 |
| Viewer | 只读查看 |

权限检查按 capability 实现，项目组可加严但不能绕过平台强制规则。归档项目组后禁止新建 Template、Version 和 Instance，已有 Run 可以完成，历史保持只读，不级联删除任何执行记录。

### Project Workspace

登记在项目组下的仓库或目录，包括显示名称、不可变 ID、规范化根路径、仓库信息和访问策略。来源可以是管理员允许根目录下的本地目录，也可以是由 Harness 克隆到受控根目录的 Git URL。它是 Run 工作目录的候选来源，不代表某次执行实际使用的目录。

### Run Workspace

用户每次创建 Run 时必须选择执行位置：

```text
Project Workspace 原目录
或新建 Git worktree
或已登记的受控 Workspace
或显式 Demo/local 模式的临时 Workspace
```

选择只对当前 Run 生效；用户可以显式勾选“保存为 Workflow 默认策略”。branch、tag 或 commit 最终解析并固化为不可变 commit SHA。选择原目录时必须检查脏修改、当前分支、写权限和其他可写 Run；存在另一个可写 Run 时直接阻止。Maintainer 显式接受脏目录后，系统记录 HEAD、staged diff、unstaged diff 和 untracked manifest 作为启动快照。

非 Git 目录可以原地执行或复制为受控快照，创建前展示文件数、预计大小和忽略规则。Run 结束后 Workspace 默认保留 7 天；有未合并 commit、未归档修改或 Artifact 时进入 `RETAINED`，不能自动清理。清理先进入保留 24 小时的 Trash。

### Attempt Workspace Binding

每个 Task Attempt 固化它实际使用的 Workspace、版本和策略。顺序任务默认绑定 Run Workspace；并行写任务根据 `write_scope` 检测冲突，必要时绑定隔离 Workspace：

```text
Project Workspace
└── Run Workspace
    ├── Attempt A Workspace Binding → Run Workspace
    ├── Attempt B Workspace Binding → Run Workspace
    └── Attempt C Workspace Binding → Isolated Workspace
```

隔离任务产出 commit 或 Patch，由显式 Merge Task 合并到 Run Workspace，不能在 Task 变为 DONE 时隐式合并。

新 UI 使用 `workspace_id`。生产模式中的绝对路径必须匹配已登记 Workspace；`--acp-workspace-root` 只作为本地模式默认值。生产任务无法解析 Workspace 时进入 `WAITING_FOR_CAPABILITY`，而不是以无效目录直接启动。`service_name` 始终只是业务标签，不参与目录解析。

显式 Demo/local 模式可以按 Run 在 `/tmp` 创建带 Run ID 的临时目录，页面必须持续显示“临时演示工作区”。

### Workspace File

通过 Attempt Workspace Binding 在对应 Workspace 中寻址的文件。浏览器永远不提交服务器绝对路径作为写入目标。

### Execution Capability

实例和节点详情页必须把“为什么能执行/为什么不能执行”作为一等信息展示：

| UI 区域 | 展示内容 | 可执行操作 |
|---|---|---|
| Capability Summary | Preflight 状态、阻塞数量、最近检查时间 | 重新预检、查看修复建议 |
| Agent Runtime | Runtime、Adapter、版本、ACP 能力协商结果 | 仅在权限允许时切换 Profile |
| Skill Bundles | 名称、来源、版本、校验摘要、注入方式 | 查看来源和不兼容原因 |
| MCP Grants | Server、工具暴露范围、调用权限、健康状态 | 查看授权、跳转治理入口 |
| Execution Manifest | Attempt 实际使用的完整快照 | 只读查看、复制摘要、审计跳转 |

缺失能力显示为 `WAITING_FOR_CAPABILITY` 或 `UNROUTABLE`，不能显示成普通 `PENDING`。Manifest 生成后只读，重试/重新执行必须显示新旧 Manifest 差异。

## 4. 总体信息架构

```text
Global Header
├── 作业流模板
│   ├── Template List
│   ├── Template Detail（只读 DAG）
│   └── Template Editor（JSON + DAG）
├── 作业流实例
│   ├── Instance List
│   ├── Instance Create
│   └── Instance Detail（运行 DAG）
├── 节点协作详情
│   ├── Project Group Sidebar
│   ├── Agent Collaboration
│   ├── Workspace File Tree
│   └── Code Editor / Runtime Panel
├── 执行日志
│   └── Instance / Run / Attempt / Session Event Explorer
└── 全局配置
    └── Agent Runtime、Skill、MCP、Workspace、权限、预算和运行策略
```

主路由建议：

| 路由 | 页面 |
|---|---|
| `/templates` | 模板列表 |
| `/templates/:templateId` | 模板详情与只读 DAG |
| `/templates/:templateId/edit` | 模板草稿编辑、JSON 和 DAG 预览 |
| `/templates/:templateId/versions/:versionId` | 指定版本详情 |
| `/templates/:templateId/instances/new` | 实例创建 |
| `/instances` | 实例列表 |
| `/instances/:instanceId` | 实例详情与运行 DAG |
| `/instances/:instanceId/tasks/:taskId` | 节点详情/协作工作台 |
| `/instances/:instanceId/attempts/:attemptId/manifest` | Execution Manifest |
| `/instances/:instanceId/logs` | 执行日志 |
| `/settings` | 全局配置 |

URL 保存当前项目组、Template/Version、Instance、Task、Attempt 和打开文件，刷新后可以恢复上下文。节点详情记录来源路由，返回时优先回到来源页；无来源时回实例详情。

## 5. 全局顶栏

高度 56–64px，固定在视口顶部。

从左到右：

1. Harness 品牌与当前部署环境；
2. 面包屑：项目组 / 模板 / 版本 / 实例 / Task；
3. 当前对象状态和完成进度；
4. 主导航：模板、实例、执行日志、全局配置；
5. Harness API、Execution Gateway、Agent Runtime 和能力预检状态；
6. 刷新、通知和用户入口；
7. 当前上下文允许的主操作，如运行、暂停、恢复、中止或重试。

顶栏不展示虚构的 Cluster 数据。接入真实运行资源指标后才显示集群选择和负载。

## 6. 项目组侧栏

默认宽度 256px，位于顶栏下方并贯穿页面。

### 内容

- 项目组搜索和新增入口；
- 项目组列表、Workflow 数量和活跃状态；
- 当前项目组摘要；
- 当前项目组下的模板和实例入口；
- “已归档”固定入口；
- 侧栏底部显示默认工作区及连接状态。

### 交互

- 切换项目组后保留用户最近打开的模板或实例；
- 支持折叠到 48px 图标栏；
- 创建、重命名、归档项目组使用弹窗或右侧检查器；
- 无管理权限时隐藏写操作，但保留浏览能力；
- 项目组不可作为 Agent 调度键，UI 中不得出现“项目组 Agent”概念；
- Execution Profile、Skill 和 MCP 只显示项目组允许范围，不显示未授权资源。

## 7. 模板与实例页面

### 7.1 模板列表

以 Project Group 为上下文展示模板名称、描述、草稿修订、最新发布版本、实例数量、更新时间和状态。行级操作：查看详情、编辑草稿、创建实例、归档；只有从未绑定实例的模板显示删除。

### 7.2 模板详情

模板详情是只读页面，首屏包含：

- 名称、描述、Project Group、模板状态和最近更新时间；
- 最新发布版本、历史版本、版本可用性和发布说明；
- 只读 DAG、节点/边数量、根叶节点和校验摘要；
- 活跃实例、历史实例和“创建实例”入口；
- 根据权限显示编辑草稿、发布、归档和删除。

点击节点打开节点说明抽屉或节点详情，但不能在详情页直接修改已发布版本。返回入口回到模板列表或来源页面。

### 7.3 模板编辑

采用左右分栏：左侧 JSON 编辑器，右侧实时 DAG；顶部显示“草稿 / 最近保存 / revision / 校验状态”。

- JSON 编辑器提供格式化、Schema 提示、错误行定位和节点定位；
- DAG 只反映当前草稿，节点点击可以回到 JSON 对应位置；
- 底部操作固定为“保存草稿”和“发布”；
- 发布弹窗明确提供“保存为草稿、仅发布、发布并创建实例、发布并执行”；
- 已发布版本只读，编辑动作必须创建或更新草稿。

### 7.4 实例创建

实例创建页先展示只读的模板、版本和 DAG 摘要，再填写本次实例配置：输入参数、Git 分支/Commit、Workspace、Execution Profile 以及稍后启动/立即执行。

提交前展示参数校验、Git 解析、Workspace 可用性和预期能力范围。实例创建成功后进入实例详情；如果选择立即执行，先展示 Capability Preflight，不直接跳过预检。

### 7.5 实例详情

实例详情顶部展示来源模板/版本、实例状态、参数摘要、Git commit、Workspace、Run 和主要控制动作；中部展示运行 DAG；右侧或底部展示 Preflight、Manifest、日志、证据和 ChangeSet。

实例详情的 DAG 是模板 DAG 的运行投影，节点颜色、文字和图标由 Task/Attempt 状态驱动。实例详情支持“修改并重新执行”，但不允许原地覆盖模板版本或历史实例。

## 8. DAG 流程编排页

页面必须根据上下文明确区分两类 DAG：

| 页面 | DAG 来源 | 是否可编辑 | 状态 |
|---|---|---|---|
| 模板详情 | 已发布 Template Version | 否 | 版本校验结果 |
| 模板编辑 | Template Draft | 是 JSON；DAG 为实时预览 | 草稿校验结果 |
| 实例详情 | Instance 绑定的 Version | 否 | Task/Attempt 运行状态 |

模板详情和实例详情可以共享画布组件，但不能共享状态 store。实例 DAG 的节点状态、日志和控制操作不能回写模板定义。

### 8.1 Template / Instance / Task 面板

位于项目组侧栏和画布之间，宽度 340–380px。

包含：

- Template/Version 或 Instance/Run 标题和状态；
- 新建任务入口；
- Task 搜索；
- 全部、运行中、完成、失败、等待人工和跳过筛选；
- Task 紧凑列表；
- 每个 Task 显示状态、类型、Agent Runtime、Attempt、时长和重试次数；
- 面板可折叠，为画布释放空间。

### 8.2 DAG 画布

使用深蓝黑底和 24px 点阵网格，填满剩余空间。

支持：

- 平移、滚轮缩放、适应窗口；
- 左右和上下布局切换；
- 自动布局与重新对齐；
- Minimap；
- 全屏；
- 导出 DAG 图片；
- 节点搜索与定位；
- 点击节点进入协作详情；
- 悬停时高亮直接上下游；
- 选中时高亮完整影响路径。

交互层使用 `@vue-flow/core`，节点外观保持自定义。首期以 100 个 Task 下的平移、缩放、筛选和选择保持流畅为目标；超过阈值时启用节点简化和局部渲染，不承诺首期支持 500 个以上节点全量展开。

### 8.3 DAG 节点卡

建议尺寸约 240 × 132px，展示：

- Task 类型、名称和 ID；
- 完整状态徽标；
- Claude/Codex Provider 与 Agent ID；
- Attempt、重试次数和执行时间；
- 输入/输出或完成门禁摘要；
- 失败时显示 `error_message` 摘要；
- “进入工作台”、重试或等待人工入口。

状态视觉：

| 状态 | 颜色与连线 |
|---|---|
| `IN_PROGRESS` | 青蓝光效、动态连线 |
| `DONE` | 绿色实线 |
| `PENDING` / `BLOCKED` | 琥珀或灰色 |
| `WAITING_FOR_HUMAN` | 紫色脉冲 |
| `WAITING_FOR_CAPABILITY` / `UNROUTABLE` | 橙色告警，显示阻塞能力和修复入口 |
| `FAILED` | 玫红边框和错误摘要 |
| `SKIPPED_UPSTREAM_FAILED` | 灰红虚线并标明上游失败 |
| `ABORTED` | 灰色终止标记 |

前端使用后端 phase 和原始 Task 状态，不再把未知状态降级为 `PENDING`。

## 9. 节点协作详情页

参考 `ui/stitch_dag/ai`，采用“对话 + 文件树 + 编辑器”的三栏主工作区。

页面顶部显示：返回来源页面、Template/Version/Instance/Task 面包屑、Task 状态、Agent Runtime、Attempt、执行时长和操作按钮。返回按钮必须始终可见。

节点详情的首屏增加 Capability 状态条：展示 Preflight、Execution Manifest、Skill Bundle 和 MCP Grant 的摘要；阻塞时提供结构化原因和修复入口。

### 9.1 Agent Collaboration

建议占可用宽度的 36%–42%。

- 展示当前 Agent Session 对话；
- 显示 Agent Update、Tool Call 和人工消息；
- 支持按 Attempt/Session 查看历史；
- 预置快捷指令，如解释失败、重新验证、检查改动；
- 底部固定人工消息输入框；
- 支持 `queue` 和 `interrupt`；
- interrupt、恢复终态 Task 时明确展示会创建新 Turn 或 Attempt；
- Agent 正在输出时提供跟随、暂停滚动和未读提示。

### 9.2 Workspace File Tree

建议占可用宽度的 20%–25%。

- 根节点明确显示 Attempt Workspace Binding 指向的 Workspace 名称，而不是直接泄露服务器绝对路径；
- 支持目录展开、文件搜索、刷新和变更标记；
- 显示 Git modified/untracked/conflict 状态；
- 支持定位 Agent 最新修改的文件；
- 二进制、大文件和被策略禁止的文件显示不可预览原因；
- 历史 Attempt 使用 Workspace Manifest、Git ref 和未提交 Patch 重建只读快照；
- 当前 Attempt 若没有工作区，展示清晰错误和修复建议。

文件树不直接访问本地文件系统，所有读取都经过 Harness Workspace API。

普通用户只看到逻辑 Workspace 名称、仓库、分支和相对路径；Maintainer/Owner 可以在诊断面板查看脱敏后的物理路径。

文件内容按类型和大小分级：UTF-8 文本不超过 2 MB 时可编辑，2–10 MB 时只读，超过 10 MB 或属于二进制时只展示元数据。限制允许管理员加严。

### 9.3 Code Editor

建议使用 Monaco Editor，约占 35%–44%，窄屏时成为独立标签页。

能力范围：

- 多文件标签；
- 语法高亮、行号、搜索和 Minimap；
- 只读/可编辑模式；
- Git Diff；
- 未保存标记；
- 显式保存；未提交内容只作为浏览器会话级草稿；
- 复制代码；
- 在外部 IDE 打开的扩展入口；
- 底部状态栏显示语言、编码、换行、Git 分支、Attempt 和工作区读写状态。

Monaco Editor 仅在第一次打开代码文件时动态加载，不进入 DAG 首屏包。首期支持读取、修改和新建策略允许路径内的文本文件；不支持重命名、移动、删除、批量替换或任意 Shell。手机端支持文件树、只读代码和 Diff，写入只在平板和桌面开放。

保存必须携带打开文件时的 `expected_sha256` 和请求 `idempotency_key`。文件已经被 Agent、外部 IDE 或其他用户修改时返回 HTTP 409，并展示 base、当前 Workspace 和人工草稿三方 Diff，不得静默覆盖。

人工修改文件后，界面提供：

1. 仅保存；
2. 保存并向 Agent 排队说明；
3. 保存并中断 Agent 立即检查。

默认选中“保存并排队说明”。保存生成 `WORKSPACE_FILE_CHANGED` 事件，来源明确为 `human`；Agent Tool Call 产生的修改标记为 `agent`，目录监控发现但无法归因的修改标记为 `external`。保存和消息是两个独立、可审计的动作，发送消息失败不能回滚已经成功的文件写入。

普通保存不自动创建 Git commit。用户完成一组修改后可以显式创建 checkpoint commit，提交信息关联 actor、Task 和 Attempt。

客户端草稿按 user/workspace/path 隔离，只在当前浏览器会话保存；退出登录、Workspace 归档或策略要求时立即清除。项目组可以完全禁用客户端代码草稿。

### 9.4 Runtime Panel

编辑器下方提供可折叠面板：

- Session Events；
- Agent 输出；
- 工具调用；
- 测试/验证结果；
- 错误和 stderr；
- Artifact 与 Evidence；
- Task 状态迁移。

“执行日志”全局页展示完整历史；节点页 Runtime Panel 只展示当前 Task/Attempt。

格式化、测试和验证通过服务端登记的受控 Tool Action 执行。每个 Action 声明固定 ID、参数 schema、cwd、超时和权限，不接收浏览器提交的任意命令字符串。

## 10. 执行日志页

提供 Template/Version、Instance、Run、Task、Attempt、Session 六级过滤：

- 时间线和流式日志两种视图；
- 按事件类型、状态、Agent Runtime、能力状态和 actor 筛选；
- 错误事件可跳转到对应 Task 和文件；
- 展示人工消息、文件写入和危险控制操作；
- 支持导出 Run Session；
- 使用 SSE 推送运行、Session 和 Workspace 事件，写操作继续使用 HTTP；客户端通过 `Last-Event-ID` 断线续传，游标过期后重新拉取当前快照。

## 11. 后端 API 设计

浏览器只能发送 `workspace_id + relative_path`，服务端负责路径规范化和授权。

Project Group 首期沿用现有 `KVStore` Protocol，保持 Consul、Local 和 FileStore 三种模式一致。Project Group、Workspace 等关联使用不可变 ID；名称可以修改，可读 slug 不能承担引用完整性。

建议接口：

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/project-groups` | 项目组列表 |
| `POST` | `/api/project-groups` | 创建项目组 |
| `PATCH` | `/api/project-groups/:id` | 更新或归档项目组 |
| `GET` | `/api/project-groups/:id/members` | 成员与角色 |
| `GET` | `/api/project-groups/:id/templates` | 项目组下的模板 |
| `GET` | `/api/project-groups/:id/instances` | 项目组下的实例 |
| `POST` | `/api/workspaces` | 登记本地目录或 Git URL |
| `POST` | `/api/instances` | 选择版本、参数、Workspace 并创建实例 |
| `GET` | `/api/instances/:id/runs/:run/workspace` | Run Workspace 信息 |
| `GET` | `/api/instances/:id/tasks/:task/attempts` | Task Attempt 列表 |
| `GET` | `/api/attempts/:attempt/workspace-binding` | Attempt Workspace Binding |
| `GET` | `/api/attempts/:attempt/execution-manifest` | Execution Manifest |
| `POST` | `/api/instances/:id/preflight` | 能力预检 |
| `GET` | `/api/execution-profiles` | 可用 Execution Profile |
| `GET` | `/api/skill-bundles` | 可用 Skill Bundle |
| `GET` | `/api/mcp-servers` | 已注册 MCP Server 和授权摘要 |
| `GET` | `/api/workspaces/:id/tree?path=` | 获取目录树 |
| `GET` | `/api/workspaces/:id/file?path=` | 读取文件及版本摘要 |
| `PUT` | `/api/workspaces/:id/file` | 带版本条件保存文件 |
| `GET` | `/api/workspaces/:id/search?q=` | 工作区文件搜索 |
| `GET` | `/api/workspaces/:id/changes` | Git/文件变更摘要 |
| `POST` | `/api/workspaces/:id/checkpoints` | 创建检查点 Commit |
| `POST` | `/api/workspaces/:id/actions/:actionId` | 执行受控 Tool Action |
| `GET` | `/api/events` | SSE 事件流 |
| `GET` | `/api/capabilities` | 前端功能能力协商 |

文件读取响应至少包含：

```json
{
  "workspace_id": "run-ws-123",
  "binding_id": "attempt-binding-456",
  "path": "src/main.py",
  "language": "python",
  "encoding": "utf-8",
  "content": "...",
  "sha256": "...",
  "writable": true,
  "modified": true
}
```

文件写入至少包含 `path`、`content`、`expected_sha256`、`idempotency_key` 和 `reason`，冲突返回 HTTP 409。`actor` 从认证上下文读取，不能信任请求正文。

前端通过 capabilities 分阶段开放能力，例如：

```json
{
  "project_groups": true,
  "workspace_browse": true,
  "workspace_write": false,
  "workspace_diff": true,
  "sse_events": true
}
```

正常模式 API 不可用时，页面保留最后一次明确标记的只读快照，并显示离线状态和重试入口。不得静默切换 Mock；Mock 只由显式 Demo 模式启用。

## 12. 安全边界

- Workspace 必须登记在允许根目录内；
- 规范化路径后再次检查边界，禁止 `..` 和符号链接逃逸；
- 平台强制规则、项目组策略和 Workspace 策略共同决定权限，后两者只能加严；
- 默认拒绝读取 `.env*`、私钥、凭据、Token、`.git` 内部文件和平台敏感路径；
- 限制单文件大小、目录深度、搜索数量和写入频率；
- 二进制文件不返回正文；
- 文件写入权限由 Project Group、Workspace 和 Attempt Binding 三层策略共同决定；
- 历史 Attempt 默认只读；
- 每次读取敏感文件、保存、重命名和删除都写审计日志；
- 删除、批量替换和覆盖冲突文件需要额外确认；
- Agent 权限与人工编辑权限分别计算，不能因为 Agent 可写就默认所有用户可写。

内网部署由 OIDC 或可信反向代理完成认证，Harness 只接受经过验证的身份声明；local 模式使用清晰标记的 local user。浏览器提交的 `actor` 不具有身份效力。

审计元数据长期保留，包括身份、动作、时间、目标、摘要和结果；可能包含源代码或秘密的文件正文、Patch 和 Diff 按项目组策略加密、过期和清理。Workspace 清理进入 24 小时 Trash，但恢复能力不代替 Git、Artifact 或 Patch。

## 13. 响应式布局

首发提供深色 Obsidian、浅色和跟随系统三种主题；所有颜色使用语义 Token，核心路径以 WCAG 2.1 AA 为目标，状态同时使用文字、图标和颜色。主题选择持久化，不影响运行上下文。

### ≥ 1440px

- 项目组侧栏 256px；
- Task 面板 360px；
- DAG 使用剩余空间；
- 节点工作台显示对话、文件树和编辑器三栏。

### 1024–1439px

- 项目组侧栏折叠为 48px；
- Task 面板可收起；
- 文件树可临时抽屉展开；
- 编辑器保持主工作面。

### < 1024px

- 项目组和 Task 列表使用侧滑层；
- DAG 全屏；
- 节点协作采用“对话 / 文件 / 代码 / 运行”四个标签页；代码仅只读和 Diff；
- 危险操作固定在更多菜单中，仍需确认。

键盘操作至少覆盖项目组和任务导航、DAG 节点选择、打开文件、保存、发送消息及确认弹窗；画布操作必须提供非指针替代方式。

## 14. 前端组件规划

```text
AppShell
├── GlobalHeader
├── ProjectGroupSidebar
├── TemplateInstancePanel
├── DagWorkspace
│   ├── DagCanvas
│   ├── DagNodeCard
│   ├── CanvasToolbar
│   └── DagMinimap
├── NodeWorkbench
│   ├── AgentCollaborationPanel
│   ├── CapabilitySummaryPanel
│   ├── ExecutionManifestPanel
│   ├── WorkspaceFileTree
│   ├── CodeEditorPanel
│   └── RuntimePanel
├── ExecutionLogExplorer
└── TemplateEditor
    ├── JsonDefinitionEditor
    └── DraftDagPreview
```

现有 `ControlDialog`、`ExecutionTimeline`、Human Message API 和 Session API 应复用并重构样式。DAG 使用 `@vue-flow/core`，节点外观保持自定义；Monaco Editor 动态加载，避免拖慢 DAG 首屏。模板编辑器使用服务端 draft/validation API；没有后端发布能力时显示为不可用，不将浏览器本地 JSON 伪装成已发布版本。

## 15. 分阶段实施

### Phase 1：模板/实例页面与运行看板

- 深色语义主题、顶栏、项目组侧栏静态结构；
- Template/Version/Instance/Task 导航；
- 模板列表、模板详情只读 DAG、模板编辑 JSON+DAG 预览；
- 实例创建、实例详情和节点来源返回；
- 基于 Vue Flow 的 DAG 画布、状态节点和控制操作；
- 修复完整状态映射和错误展示；
- 保持现有 WebAPI 可运行；
- 未实现能力由 `/api/capabilities` 控制，显示清晰的禁用状态。

### Phase 2：能力状态和项目组

- Project Group 数据模型和 CRUD；
- Workflow Template 主归属和归档；
- OIDC/可信代理身份和固定角色 capability；
- Project Workspace 登记；
- 项目组权限与审计；
- Execution Profile、Capability Preflight、Skill/MCP 摘要和 Manifest 只读展示。

### Phase 3：Run Workspace 与节点协作工作台

- Run 启动工作区和 Git 基线选择；
- Run Workspace 与 Attempt Workspace Binding；
- 原目录保护、worktree、非 Git 快照和保留策略；
- 独立 Task 路由；
- Agent Session 对话；
- Attempt 切换；
- Human Message 与 Runtime Panel；
- 从 DAG、日志和通知深链进入 Task。

### Phase 4：文件树与只读 Monaco

- 历史 Workspace Manifest、Git ref 和 Patch；
- 安全的目录树、文件读取和搜索 API；
- Monaco 动态加载、只读浏览、多标签和三方 Diff；
- Agent 文件变更定位。

### Phase 5：人工代码编辑

- 文件写入、冲突检查和审计；
- 保存并通知/中断 Agent；
- 新建文本文件与 checkpoint commit；
- 受控 Tool Action；
- 权限、敏感文件策略和浏览器会话草稿；
- 文件操作来源归因并与 Session 时间线关联。

### Phase 6：实时性与生产化

- 支持断点续传的 SSE；
- trace/span、token、cost 和 latency；
- 通知中心；
- 超过 100 个 Task 的 DAG 简化和局部渲染；
- 无障碍和端到端测试。

## 16. 验收标准

1. 用户能按主项目组找到模板和实例，并能区分草稿、版本和实例；
2. 模板详情、模板编辑和实例详情能分别展示只读 DAG、JSON+DAG 预览和运行 DAG；
3. 所有后端 Task 状态在页面中无损显示；
4. 点击任意 DAG 节点可进入稳定 URL 的节点工作台；
5. 用户能查看完整 Attempt/Session 历史并发送 queue/interrupt 消息；
6. 文件树使用当前 Attempt Workspace Binding，并与 Agent 使用的 Workspace 完全一致；
7. 用户能打开、搜索和比较文本文件，受限文件不会泄露内容；
8. 并发修改文件时不会静默覆盖，冲突明确返回并可比较；
9. 人工保存代码、文件变更来源及随后发送给 Agent 的消息均可审计；
10. 创建 Run 时用户可以选择工作区和 Git 基线，并可显式保存为默认策略；
11. 并行写范围冲突会被阻止或使用隔离 Workspace，并通过 Merge Task 收口；
12. 桌面端能编辑代码，移动端能查看文件、Diff 并介入 Task；
13. SSE 断线后可以续传，游标过期时能从一致快照恢复；
14. API 不可用时明确展示离线状态，Demo 数据始终标记为模拟；
15. 100 个 Task 的 DAG 主要交互保持流畅；
16. 节点详情显示 Capability Preflight 和 Execution Manifest，缺失能力不会被显示为普通 Pending；
17. Light/Dark/System 主题刷新后保持，核心路径可通过键盘完成并达到 WCAG 2.1 AA 目标；
18. `npm run check`、生产构建、组件测试和关键 E2E 全部通过。

## 17. 明确非目标

- Dashboard 不取代完整桌面 IDE；
- 首期不实现任意终端命令执行，只提供受控 Tool Action；
- 首期不支持文件重命名、移动、删除和批量替换；
- 不允许浏览 Attempt Workspace Binding 所指 Workspace 之外的主机文件；
- Project Group 不承担 Agent 调度职责；
- `service_name` 不承担 Workspace 定位职责；
- 文件树不是 Artifact 存储的替代品；
- 代码编辑器中的未保存内容不会自动作为 Agent 上下文；
- 回收站不是 Git、Artifact 或 Patch 的替代品；
- UI 改版不改变 Template、Version、Instance、Task、Attempt 和 Session 的状态所有权。
- UI 不直接启动 Agent，不绕过 Execution Gateway，也不直接访问 Consul 或 MCP Server。
