# Harness Orchestration 产品需求文档

> 版本：v1.1  
> 状态：当前产品基线，供产品、设计与研发评审  
> 更新日期：2026-09-21  
> 适用范围：人工编排的作业流模板、模板版本、作业流实例及执行闭环

## 输入历史 (Input History)

> 本章节按时间顺序记录驱动本 PRD 演进的所有原始用户输入，作为需求来源的可追溯凭据。**请勿删除或改写已有条目**，新输入一律追加到末尾。

### 2026-10-01 · Clarify · Q1 Run 生命周期
**模式**: clarify
**问题**: 什么触发新 Run？
**原始回答**:
> A

### 2026-10-01 · Clarify · Q2 草稿并发控制
**模式**: clarify
**问题**: 多人同时编辑同一模板草稿时的并发策略？
**原始回答**:
> A

### 2026-10-01 · Clarify · Q3 Workspace 生命周期
**模式**: clarify
**问题**: Workspace 的创建、清理和保留策略？
**原始回答**:
> B

### 2026-10-01 · Clarify · Q4 权限模型
**模式**: clarify
**问题**: Project Group 内的角色定义？
**原始回答**:
> A

### 2026-10-01 · Clarify · Q5 产物存储与保留
**模式**: clarify
**问题**: 产物的保留策略？
**原始回答**:
> B

## 1. 产品定义

Harness Orchestration 是一个面向研发团队的作业流模板编排与执行平台。用户以人工方式定义任务节点和依赖关系，形成可复用的作业流模板；模板发布为不可变版本后，可以创建多个相互隔离的作业流实例。

当前产品的核心闭环是：

```text
选择项目组 → 创建模板 → 编辑节点定义 → 预览/校验 DAG
  → 保存草稿 → 发布模板版本 → 创建多个实例
  → 按依赖执行 → 查看任务与证据 → 重试、干预或变更重执行
```

本期不把大模型作为 DAG 创建的必要环节。后续可以增加大模型辅助编排，但必须以提案、校验和人工确认的方式接入，不能绕过版本冻结和发布确认。

## 2. 产品目标与边界

### 2.1 产品目标

1. 让用户可以在一个项目组内创建、校验、发布和复用作业流模板。
2. 明确区分“模板定义”和“实例执行”，避免多个实例之间串数据、串状态或串 Workspace。
3. 让用户能同时理解机器可执行的 JSON 定义和人可阅读的 DAG 结构。
4. 发布新版本时不影响已经创建的旧版本实例。
5. 支持从运行中的实例发起变更，生成后继实例并重新执行，同时保留完整历史。
6. 对发布、删除、中止和重新执行等高风险操作提供确认、权限和审计。

### 2.2 本期范围

- Project Group 上下文与权限边界；
- 作业流模板创建、编辑、保存草稿、校验、发布、归档和删除；
- 模板版本的不可变快照和版本可用性管理；
- 一个模板创建多个实例，实例并行执行且上下文隔离；
- 实例输入参数、Git 分支/Commit、Workspace 的独立配置；
- DAG 预览、任务详情、Task Attempt、Agent Session、日志和产物引用；
- 实例暂停、恢复、中止、Task 重试、人工消息和变更重执行；
- Light / Dark 主题和节点详情页返回路径；
- API、UI 自动化和关键业务流程的测试覆盖。

### 2.3 本期不包含

- 大模型自动生成、修改或解释 DAG；
- `dev → staging → production` 三环境模型和环境晋级；
- 通用项目管理、工时、绩效和团队排期；
- 替代 Git、CI/CD、日志或制品系统；
- 模板市场、跨项目组共享和多人草稿合并；
- 定时、Webhook 或外部事件触发实例。

## 3. 核心领域模型

### 3.1 对象关系

```text
Project Group
  └── Workflow Template
        └── Template Version
              └── Workflow Instance
                    └── Run
                          └── Task Attempt
```

对象职责如下：

| 对象 | 产品含义 | 是否执行任务 |
|---|---|---|
| Project Group | 模板、实例、权限和 Workspace 的边界 | 否 |
| Workflow Template | 可复用的作业流定义，拥有稳定 ID | 否 |
| Template Version | 模板发布后的不可变快照 | 否 |
| Workflow Instance | 绑定一个模板版本的一次具体使用 | 是 |
| Run | 实例的一轮执行记录 | 是 |
| Task | 模板版本中的逻辑节点 | 否，定义执行单位 |
| Task Attempt | Task 的一次实际执行 | 是 |

一个模板可以创建多个实例；实例之间的输入参数、Git 分支或 Commit、Workspace、Task 状态、Attempt、日志和产物引用都必须隔离。

**Run 生命周期**：一个 Instance 只有一个 Run。暂停后恢复不创建新 Run，仅在"变更重执行"时创建 successor instance（整个实例级别）。Run 的状态随 Instance 的启动/暂停/中止/终态同步转换。

### 3.2 JSON 与 DAG 的关系

- JSON 是模板定义的输入和持久化表达；
- 服务端负责规范化任务数组、任务对象映射和依赖关系，并执行结构校验；
- DAG 是规范化定义的可视化投影，不是第二份独立数据源；
- 模板编辑页展示 JSON 编辑器和实时 DAG 预览；
- 模板详情页展示只读 DAG、当前版本和校验结果；
- 草稿 DAG 可以变化，已发布版本的 DAG 必须不可变；
- 实例详情页展示的是该实例的运行状态投影，不应与模板 DAG 混为一谈。

### 3.3 模板、版本与实例边界

- 模板是可编辑容器，不直接执行任务；
- 发布动作生成新的不可变模板版本；
- 已发布版本不能原地修改；
- 已有实例固定绑定创建时的 `version_id`；
- 发布 V2 不影响已有 V1 实例；
- Task 重试仍属于原实例，只新增 Task Attempt；
- 整个实例修改并重新执行时，创建新的 successor instance，旧实例保留审计历史。

### 3.4 Agent 执行与能力模型

作业流只声明“任务需要什么能力”，不把某个 Agent、Skill 或 MCP 实现硬编码进模板。一次 Task Attempt 在启动前解析出不可变的执行清单：

```text
Task Capability Requirements
          + Execution Profile
          + Skill Bundles
          + MCP Grants
          + Agent Runtime / Adapter
          + Workspace / Secret Policy
                    ↓
          Execution Manifest（Attempt 快照）
                    ↓
              ACP Session / Agent
```

| 概念 | 产品职责 | 是否由平台维护实现内容 |
|---|---|---|
| Task Capability Requirements | 描述任务需要的抽象能力、资源、完成门禁和风险级别 | 平台维护 schema，不维护所有实现 |
| Execution Profile | 项目组允许使用的运行时、Workspace、预算、网络和安全策略 | 平台维护可选配置和版本 |
| Skill Bundle | 可版本化的 Agent 指令、规则和辅助资源 | 可由平台、项目组或外部仓库提供 |
| MCP Server / Grant | 外部工具或数据服务，以及本次执行允许暴露/调用的权限 | 平台维护注册、版本、权限和健康状态 |
| Agent Runtime / Adapter | 具体 ACP Agent 启动方式和协议适配 | 平台维护适配器；Agent 实现由提供方维护 |
| Execution Manifest | 本次 Attempt 实际采用的全部解析结果 | 平台生成并冻结，供审计和重试使用 |

平台负责引用、版本、授权、预检、审计和运行时装配，不负责把所有 Skill、MCP Server 或 Agent 实现集中收编为一个巨型能力库。

## 4. 状态与关键规则

### 4.1 模板与版本

模板状态至少包括：`DRAFT`、`PUBLISHED`、`ARCHIVED`。版本状态至少包括：`DRAFT`、`PUBLISHED`、`DISABLED`、`ARCHIVED`。

- 未绑定任何实例的模板允许物理删除；
- 只要模板曾经绑定过实例，就不能物理删除，只能归档；
- 已归档项目组不能创建或修改新的模板、版本和实例；
- 已禁用或归档的版本不能创建新实例，但历史实例仍可查询。

### 4.2 实例

实例状态与 Task 状态分离：

```text
QUEUED → RUNNING → DRAINING → SUCCEEDED
                    ├────────→ FAILED
                    ├────────→ ABORTED
                    └────────→ SUPERSEDED

RUNNING ⇄ PAUSED
```

- `QUEUED`、`SUCCEEDED`、`FAILED`、`ABORTED`、`SUPERSEDED` 和 `ARCHIVED` 实例可删除；
- `RUNNING`、`PAUSED` 和 `DRAINING` 实例不可直接删除，应先中止或等待进入终态；
- 删除实例只清理该实例的数据，不影响模板、模板版本和其他实例；
- 删除操作必须由服务端再次校验状态、权限并记录审计。

### 4.3 发布动作

发布确认层提供三个明确动作：

1. 保存为草稿：保存当前定义，不产生版本，不创建实例；
2. 仅发布：生成模板版本，不创建实例，不执行任务；
3. 发布并创建实例：生成模板版本并进入实例创建流程，实例创建完成后才执行。

发布与实例创建均需幂等键、权限校验、显式确认和审计记录。

## 5. 核心用户旅程

### 5.1 创建模板并发布

1. 用户进入 Project Group 上下文，点击创建模板；
2. 输入名称、描述，手工增加或编辑节点 JSON；
3. 系统规范化 JSON 并实时生成 DAG 预览；
4. 用户点击节点查看任务详情，处理依赖、Agent、完成条件和可选的 `service_name`；
5. 用户保存草稿，或进入发布确认；
6. 服务端校验节点 ID、依赖、成环、Agent、完成条件、参数引用和副作用声明；
7. 用户选择保存草稿、仅发布或发布并创建实例；
8. 发布成功后进入模板详情页或实例创建流程。

### 5.2 模板详情与模板编辑

模板详情页是发布版本的只读说明和使用入口，应展示：

- 模板名称、描述、Project Group、状态和更新时间；
- 当前发布版本、可选历史版本和版本状态；
- 只读 DAG；
- 节点数量、根/叶节点、校验结果和风险提示；
- 活跃实例数、历史实例入口和创建实例入口；
- 编辑草稿、复制模板、归档或删除等权限相关操作。

模板编辑页负责修改草稿，应展示：

- 名称、描述和参数 Schema；
- JSON 定义编辑器；
- JSON 到 DAG 的实时预览；
- 节点级和边级校验错误；
- 草稿保存状态、撤销/重做和进入发布按钮。

详情页与编辑页不能混为一个页面：详情页默认只读，编辑页才允许改变草稿。

### 5.3 创建多个实例

1. 用户从模板详情页选择一个可用版本；
2. 填写该实例的输入参数、Git 分支或 Commit、Workspace；
3. 项目组从模板继承并只读展示，不能由请求参数覆盖；
4. 保存实例配置并选择稍后启动或立即执行；
5. 系统创建独立实例，实例之间不共享可变状态；
6. 并发资源不足时实例进入 `QUEUED`，不应被误认为执行失败。

### 5.4 观察、干预与删除实例

实例详情页展示模板版本、运行上下文、DAG 进度、Task Attempt、Session、日志、证据和产物。用户可以暂停、恢复、中止、重试 Task，或发送人工消息。

节点详情页必须提供明确返回入口：优先回到用户进入节点前的模板详情、模板编辑或实例详情页；没有来源信息时回到实例详情页。

### 5.5 从运行实例修改并重新执行

用户从实例详情发起“修改并重新执行”，选择：

- 仅修改本次实例：创建实例级 ChangeSet，继续使用原模板版本；
- 修改模板：基于模板版本创建新草稿，发布后形成新版本。

系统分析直接变更、间接受影响、必须重跑和可复用的节点。确认后先创建 successor instance；successor 创建失败时，旧实例不得丢失或被错误中止。旧实例与 successor 的关系、变更内容和复用依据必须可追溯。

### 5.6 执行前能力预检

实例启动前，系统按 Task 逐个执行 capability preflight：

1. 解析实例的输入参数、Git 引用、Workspace 和项目组执行策略；
2. 将 Task 的能力需求解析为 Execution Profile、Skill Bundle、MCP Grant 和 Agent Runtime；
3. 校验 Agent Runtime 可启动、Skill Bundle 版本可读取、MCP Server 健康且权限允许、Workspace 可用、预算和安全策略满足要求；
4. 生成并保存 Execution Manifest；
5. 所有阻塞项通过后才进入 `QUEUED`/`RUNNING`，否则进入 `WAITING_FOR_CAPABILITY`，展示可修复原因，不得伪装为普通执行失败。

预检是可重复的，但已生成的 Attempt Manifest 不因平台默认配置变化而静默改变。重试或重新执行必须生成新的 Attempt Manifest，并记录差异。

## 6. 功能需求

### 模板与版本

- FR-TPL-01：用户可以在指定 Project Group 下创建空白模板。
- FR-TPL-02：用户可以编辑名称、描述、节点定义、依赖、Agent、完成条件和参数 Schema。
- FR-TPL-03：系统支持保存草稿、恢复草稿、撤销/重做和并发控制。
- FR-TPL-04：草稿保存使用乐观锁（revision 字段），保存请求携带当前 revision，服务端比对不匹配时返回 409 Conflict，客户端提示"草稿已被他人修改，请刷新后重试"。
- FR-TPL-05：模板详情页展示只读 DAG；模板编辑页展示 JSON 和实时 DAG 预览。
- FR-TPL-06：发布生成不可变版本，V2 发布不能改变 V1 或已创建实例。
- FR-TPL-07：模板删除遵守"无实例可删除、有实例只能归档"的规则。

### DAG 与校验

- FR-DAG-01：服务端将 JSON 规范化为唯一的任务和依赖模型。
- FR-DAG-02：校验重复 ID、自依赖、悬空依赖、环、空必填字段和无效 Agent。
- FR-DAG-03：校验错误定位到节点或边，并阻止发布。
- FR-DAG-04：DAG 展示节点、连线、根/叶节点和节点详情；实例页另行展示运行状态。

### 实例与执行

- FR-INS-01：同一模板版本可以创建多个实例并行运行。
- FR-INS-02：每个实例保存独立的参数、Git、Run Workspace 和执行上下文快照。
- FR-INS-03：实例创建、启动、重试、重跑和删除支持幂等或状态保护。
- FR-INS-04：Task 重试产生新 Attempt；整实例重跑产生 successor instance。
- FR-INS-05：实例删除按状态执行，删除前二次确认并审计。
- FR-INS-06：Run Workspace 在实例创建时初始化（按 ADR §5 的隔离等级选择 ORIGINAL/GIT_WORKTREE/CONTROLLED_COPY/DEMO_TEMP）；实例进入终态后保留 N 天（N 由项目组配置，默认 7），到期自动清理；清理前通知项目组管理员。ORIGINAL 级别的 Run Workspace 不参与自动清理，仅允许项目组 Admin 手动释放。Attempt Workspace Binding 不可变，Workspace 清理后 Binding 保留审计记录但路径标记为 EXPIRED。
- FR-INS-07：项目组 Admin 可以删除从未被运行中实例锁定的 Project Workspace。删除前二次确认并审计；已存在的 Run Workspace Binding 和审计记录保留不删除。

### 执行能力与 ACP

- FR-CAP-01：Task 可以声明抽象能力需求、资源预算、完成门禁和风险级别，不直接依赖平台内部 Agent 实例。
- FR-CAP-02：项目组可以选择受控的 Execution Profile；实例可以在创建时覆盖允许的参数，但不能越过项目组权限边界。
- FR-CAP-03：执行可以绑定版本化 Skill Bundle；平台记录来源、版本和校验摘要，Skill 内容不作为秘密写入日志。
- FR-CAP-04：执行可以绑定 MCP Server 和细粒度 Grant，区分“允许暴露”与“实际调用”；未授权工具不得进入 Agent 会话。
- FR-CAP-05：系统通过 ACP Adapter 启动 Agent，并在 ACP 会话建立时注入已授权 MCP 配置；Skill 通过运行时约定的工作区规则、项目配置或 Agent Provider 适配方式注入。
- FR-CAP-06：ACP 没有统一的 Skill 参数时，平台不能假设所有 Agent 都支持同一种注入方式；适配器必须声明能力并在预检阶段拒绝不满足的组合。
- FR-CAP-07：每个 Task Attempt 必须保存 Execution Manifest，包含运行时、Skill、MCP、Workspace、策略版本和解析来源。
- FR-CAP-08：能力缺失、版本不兼容、MCP 不健康或 Workspace 不可用时进入 `WAITING_FOR_CAPABILITY` 或 `UNROUTABLE`，支持修复后重新预检。
- FR-CAP-09：平台不集中维护所有 Skill/MCP/Agent 的实现，只维护可引用的元数据、版本、权限、健康检查和适配器。
- FR-CAP-10：stage-bridge 不再作为执行或兼容路径；任务上下文、产物、证据和状态反馈由统一 Execution Gateway/ACP 链路承载。
- FR-CAP-11：产物（日志、文件、证据）采用独立保留策略：实例进入终态后保留 N 天（N 由项目组配置，默认与 Workspace 保留天数对齐），到期自动清理；清理前通知项目组管理员。

### 变更、安全与审计

- FR-CHG-01：支持实例级 ChangeSet 和模板级新版本变更。
- FR-CHG-02：变更前显示影响范围、重跑范围和产物复用依据。
- FR-CHG-03：发布、归档、删除、中止和重新执行需要权限、确认和审计。
- FR-CHG-04：Project Group 是模板和实例的权限边界；实例不能跨组移动。
- FR-CHG-05：Project Group 内采用三级角色模型：
  - **Admin**：管理项目组成员、删除项目组、配置 Workspace 保留策略和 Execution Profile；
  - **Editor**：创建/编辑/发布模板、创建/启动/暂停/中止实例、发起变更重执行；
  - **Viewer**：只读访问模板、版本、实例、Task Attempt、日志和产物。
- FR-CHG-05：敏感参数使用 Secret Reference，不写入模板、历史、Prompt 或日志。

## 7. 页面与信息架构

| 页面 | 主要职责 | 不负责的事情 |
|---|---|---|
| 模板列表 | 按 Project Group 查询模板，进入详情/编辑/实例 | 不展示实例运行细节 |
| 模板详情 | 查看版本、只读 DAG、校验结果、实例入口 | 不直接修改已发布版本 |
| 模板编辑 | 编辑草稿、JSON、DAG 预览、校验、发布 | 不展示某个实例的运行状态 |
| 实例列表 | 查询实例、状态筛选、删除可删除实例 | 不修改模板定义 |
| 实例创建 | 选择版本并配置参数、Git、Workspace | 不改变模板版本 |
| 实例详情 | 观察和控制一次实例执行 | 不原地覆盖历史实例 |
| 节点详情 | 查看某个 Task/Attempt 的执行信息 | 不丢失来源页面返回路径 |

所有页面支持 Light / Dark 模式，主题选择持久化，状态不能只依赖颜色表达。

## 8. 当前实现与产品缺口

以下是从产品设计和开发落地角度，对当前代码与目标闭环的差距分级。缺口不代表本次全部实现，而是后续排期依据。

### P0：没有这些能力就难以形成稳定闭环

1. **模板详情页和路由**：当前模板入口主要进入编辑页，缺少独立的只读详情页、版本选择、只读 DAG 和实例入口。
2. **DAG 可视化协议与组件**：后端已有 JSON 规范化和校验基础，但前端仍需要统一的 graph model、布局、缩放、节点选中、错误定位和主题适配。
3. **草稿与已发布版本的视图边界**：需要明确当前草稿、最新发布版本、历史版本和版本状态，避免用户误以为编辑会改变 V1。
4. **实例创建页**：需要正式承载版本选择、参数校验、Git、Workspace、稍后启动/立即执行，而不是由模板编辑流程隐式创建空实例。
5. **发布动作的事务语义**：保存草稿、仅发布、发布并创建实例必须有明确的状态转换、幂等键、失败恢复和错误提示。
6. **Project Group 上下文体验**：列表筛选、详情展示、无上下文提示、跨组拒绝和归档组禁写需要形成一致的 UI/API 契约。
7. **执行聚合与实例隔离**：Aggregator、RunManager、Dispatcher、Watchdog 必须全链路以 `instance_id + run_id` 隔离，不能继续以模板或旧 workflow 标识作为执行身份。
8. **任务定义完整性**：需要正式确定并校验节点字段，包括 Agent、完成条件、参数引用、资源预算、恢复策略、幂等声明和补偿任务；不能只校验最小的名称与依赖。
9. **运行实例变更闭环**：需要落地 ChangeSet、影响分析、successor 创建、旧实例 drain/abort 规则和复用依据，避免“修改并重新执行”变成隐式覆盖。
10. **高风险操作审计**：发布、删除、归档、中止、重试和重新执行需要统一的审计事件、操作者、原因和结果。
11. **执行能力装配链路**：当前 ACP 调度、Skill/MCP 注入、Execution Profile、能力预检和 Manifest 尚未形成统一闭环；新作业流执行不能继续依赖 stage-bridge。
12. **新旧执行模型未收敛**：当前 `jobflows/` 写入路径与 ACPDispatcher 扫描的旧 `workflows/` 路径存在断裂，必须在实现阶段统一为 Template/Version/Instance/Run/Attempt 身份。

### P1：影响可用性、可维护性和推广

1. **JSON 编辑器体验**：Schema 提示、格式化、错误行定位、节点定位、输入法/粘贴容错和大文档性能。
2. **版本差异查看**：展示两个模板版本的节点、依赖、参数和执行策略差异。
3. **实例参数表单**：从参数 Schema 自动生成表单，支持敏感值引用、默认值、枚举和校验。
4. **Git/Workspace 约束**：明确分支、Commit、Workspace 的可选范围、存在性校验、锁定时机和失败提示。
5. **实例时间线与可观测性**：统一展示状态变化、Task Attempt、人工消息、控制动作、日志和产物链接。
6. **权限矩阵和审批策略**：三级角色（Admin/Editor/Viewer）映射到查看、编辑、发布、创建实例、控制实例、删除和管理 Workspace 操作。
7. **错误模型与恢复**：统一 API 错误码、字段级错误、可重试错误、不可重试错误和人工处理建议。
8. **自动化测试门禁**：补齐 API 契约、UI 自动化、模板/实例隔离、V1/V2 兼容、删除状态和变更重执行场景。
9. **OpenAPI/类型契约**：让前后端共享模板、版本、实例、DAG、Run、Task Attempt 和 ChangeSet 的类型定义。
10. **能力可见性**：补齐 Agent Runtime、Execution Profile、Skill Bundle、MCP Grant、预检结果和 Execution Manifest 的查询与修复入口。

### P2：规模化和智能化能力

1. 大模型生成或修改 DAG，并以提案/Diff/人工确认接入；
2. 定时、Webhook 和事件触发实例；
3. 环境、晋级和发布策略；
4. 多人协作草稿、分支和合并；
5. 模板市场、跨项目组复用和模板依赖；
6. 产物血缘、自动影响分析和更精细的局部重跑。

## 9. API 产品契约方向

当前 API 以模板、版本和实例为核心资源，建议保持以下资源边界：

| 资源 | 典型接口 | 说明 |
|---|---|---|
| Template | `GET/POST/PATCH/DELETE /api/templates` | 草稿、模板元数据和删除/归档 |
| Template Version | `GET /api/templates/:id/versions`、`POST /publish` | 发布后的不可变快照 |
| Validation | `POST /api/templates/:id/validate` | 节点/边错误和图指标 |
| Instance | `GET/POST/DELETE /api/instances` | 继承模板组，保存独立上下文 |
| Run Control | `start/pause/abort/rerun` | 实例生命周期控制 |
| Task | `/api/instances/:id/tasks` | Task、Attempt、Session 和日志 |
| ChangeSet | `/api/instances/:id/changesets` | 实例级和模板级变更分析 |
| Execution Profile | `/api/execution-profiles` | 可选择的运行策略和安全边界 |
| Capability Preflight | `/api/instances/:id/preflight` | 执行前能力检查与修复建议 |
| Execution Manifest | `/api/attempts/:id/execution-manifest` | Attempt 实际解析快照 |

约束：

- 受保护模式下列表接口必须带 Project Group 上下文；
- 实例的 Project Group 由模板归属推导，请求体不能覆盖；
- 发布、创建实例、启动和变更重执行使用幂等键；
- API 错误必须可以定位到资源、节点、字段或状态转换；
- 当前项目尚未发布，不设计旧版本接口兼容层；新实现直接以 Template/Version/Instance 资源语义为准，不再扩展旧的 Workflow/Requirement/Plan 接口。

## 10. 非功能要求

- 所有写操作记录 actor、时间、请求 ID、版本或状态前置条件；
- 草稿保存具备 revision 并发控制；
- 草稿保存采用乐观锁：客户端提交 revision，服务端校验不匹配时返回 409，提示用户刷新后重试，不使用悲观锁或最后写入胜出策略；
- 发布版本和实例执行上下文不可被静默覆盖；
- 同模板多实例运行时任务状态、日志、Workspace 和产物引用不得串扰；
- 外部副作用任务必须声明幂等或补偿机制；
- 页面刷新或重新进入后能够恢复可靠的草稿、版本和实例信息；
- Light / Dark 模式下 DAG、状态、编辑器和节点详情保持可读；
- 核心流程可通过键盘操作，状态不能只依赖颜色；
- API、UI 自动化和关键执行流程测试纳入发布门禁。
- 执行能力预检、Manifest 冻结、ACP 会话装配和 MCP 权限审计纳入发布门禁。

## 11. 当前版本验收标准

1. 用户可以在项目组上下文中创建模板并保存草稿。
2. 用户可以编辑 JSON，系统能生成可阅读的 DAG 并定位结构错误。
3. 模板详情页能只读展示版本信息和 DAG，模板编辑页能修改草稿。
4. 用户可以选择保存草稿、仅发布或发布并创建实例。
5. 同一个模板版本可以创建多个实例，实例上下文和执行状态互不污染。
6. 发布 V2 后，已有 V1 实例继续使用 V1，不被覆盖。
7. QUEUED 和终态实例可以删除，运行中实例不能直接删除。
8. Task 重试新增 Attempt，整实例重跑创建 successor instance。
9. 用户可以从运行实例发起变更，看到影响范围并保留旧实例历史。
10. 节点详情页提供返回原模板或实例页面的入口。
11. 高风险操作有权限校验、二次确认和审计记录。
12. Light / Dark 模式可以切换并在刷新后保持。
13. API 和 UI 自动化用例覆盖模板/实例隔离、版本兼容、删除状态和关键发布路径。
14. Task Attempt 能展示能力预检结果和 Execution Manifest；缺失能力不会静默启动。
15. ACP Agent 可以获得已授权的 MCP 配置和符合适配器声明的 Skill Bundle；未授权工具不会暴露。

## 12. 分阶段排期建议

- **M0：产品闭环补齐**：模板详情页、DAG 只读/预览、实例创建页、版本视图和统一发布动作。
- **M1：执行可信度**：实例隔离的运行聚合、参数/Git/Workspace 校验、审计、错误模型和 API/UI 测试门禁。
- **M2：变更可控**：ChangeSet、影响分析、successor、drain/abort、产物复用和版本 Diff。
- **M3：平台化**：权限审批、触发器、环境策略、多人协作和模板复用。
- **M4：智能化**：大模型生成/修改提案，经过服务端校验和人工确认后进入发布流程。
