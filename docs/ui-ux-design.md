# Harness Orchestration UI/UX 设计

> 版本：v2.0
> 状态：与 PRD v1.1 + 澄清结果对齐的页面级设计
> 更新日期：2026-10-01
> PRD：[`product-requirements.md`](product-requirements.md)
> 技术栈：Vue 3 · Tailwind CSS 4 · Pinia · Vue Flow · Monaco Editor
> 上版基线：[`dashboard-ui-design.md`](dashboard-ui-design.md)（保留为组件与安全参考）

## 1. 设计范围与 PRD 需求映射

本文将 PRD 的全部功能需求映射到具体页面、组件和交互流。上版基线中的安全模型、组件规划和响应式断点继续有效，本文不重复。

| PRD 需求 | 页面/组件 | 本文章节 |
|---|---|---|
| FR-TPL-01~07 模板与版本 | 模板列表 / 详情 / 编辑 / 发布确认 | §3 |
| FR-DAG-01~04 DAG 与校验 | DAG 画布 / 校验面板 | §4 |
| FR-INS-01~06 实例与执行 | 实例列表 / 创建 / 详情 | §5 |
| FR-CAP-01~11 执行能力与 ACP | 能力预检 / Manifest / 节点工作台 | §6 |
| FR-CHG-01~05 变更与审计 | ChangeSet / 审计 / 权限矩阵 | §7 |
| 非功能要求 | 全局横切面 | §8 |

## 2. 角色与权限映射

PRD 定义三级角色。所有页面的操作按钮、侧栏入口和菜单项按角色条件渲染。

| 操作 | Admin | Editor | Viewer |
|---|---|---|---|
| 管理项目组成员 | ✅ | — | — |
| 删除项目组 | ✅ | — | — |
| 配置 Workspace 保留策略 | ✅ | — | — |
| 配置 Execution Profile | ✅ | — | — |
| 创建/编辑模板 | — | ✅ | — |
| 发布版本 | — | ✅ | — |
| 创建/启动/暂停/中止实例 | — | ✅ | — |
| 发起变更重执行 | — | ✅ | — |
| 查看模板/版本/实例/日志/产物 | ✅ | ✅ | ✅ |

无权限的操作隐藏按钮，不显示灰色禁用。Viewer 看到的是纯只读视图，所有操作栏不渲染。

## 3. 模板与版本页面

### 3.1 模板列表（`/templates`）

布局：项目组侧栏 + 全宽表格。

表格列：名称 · 描述 · 草稿修订 · 最新发布版本 · 实例数量 · 更新时间 · 状态（DRAFT / PUBLISHED / ARCHIVED）· 操作。

行级操作按权限渲染：

| 操作 | 角色 | 条件 |
|---|---|---|
| 查看详情 | 全部 | — |
| 编辑草稿 | Editor+ | 模板状态 ≠ ARCHIVED |
| 创建实例 | Editor+ | 有可用版本 |
| 归档 | Editor+ | 模板状态 ≠ ARCHIVED |
| 删除 | Editor+ | 从未绑定实例 |

顶部工具栏：搜索、状态筛选（全部/草稿/已发布/已归档）、"创建模板"按钮（Editor+）。

空状态：显示引导文案和"创建第一个模板"按钮。

### 3.2 模板详情（`/templates/:templateId`）

只读页面，布局从上到下：

**元信息条**：名称 · 描述 · Project Group · 状态 · 更新时间。右侧操作按钮（编辑草稿/发布/归档/删除），按权限渲染。

**版本选择条**：横向标签页列出所有版本（V1 / V2 / V3…），每个标签显示版本号、状态（PUBLISHED / DISABLED / ARCHIVED）和发布时间。选中的版本内容在下方展示。

**只读 DAG 画布**：占页面主体，节点不可拖拽。节点点击弹出只读详情抽屉。

**校验摘要条**：节点数 · 边数 · 根/叶节点数 · 校验结果（PASS / N ERRORS）。校验错误以徽标显示在 DAG 节点上。

**实例区域**：活跃实例数、历史实例入口和"创建实例"按钮。

### 3.3 模板编辑（`/templates/:templateId/edit`）

左右分栏：

**左侧（50%）**：Monaco JSON 编辑器。

- Schema 驱动的自动补全
- 格式化、撤销/重做
- 错误行定位和错误列表
- 节点搜索（Ctrl+F 或顶部搜索框）

**右侧（50%）**：Vue Flow DAG 实时预览。

- JSON 变更后 300ms 防抖刷新 DAG
- DAG 节点点击滚动到 JSON 编辑器对应行
- 支持平移、缩放、适应窗口、布局切换

**顶部状态条**：草稿 · 最近保存时间 · revision #N · 校验状态（✓ 通过 / ⚠ N 个错误）。

**底部固定操作栏**：

| 按钮 | 角色 | 行为 |
|---|---|---|
| 保存草稿 | Editor+ | POST draft，携带 revision |
| 发布 | Editor+ | 打开发布确认弹窗 |

#### 草稿保存冲突处理（FR-TPL-04 乐观锁）

保存请求携带当前 `revision`。服务端比对不匹配时返回 HTTP 409：

```
┌──────────────────────────────────────────────┐
│ ⚠ 草稿已被他人修改                           │
│                                              │
│ 你的修改基于 revision #12，当前为 revision #14│
│                                              │
│ [放弃修改并刷新]  [下载我的版本]  [取消]      │
└──────────────────────────────────────────────┘
```

冲突弹窗不提供"强制覆盖"选项。用户必须选择放弃自己的修改或下载后手动合并。

#### 发布确认弹窗

| 选项 | 效果 |
|---|---|
| 保存为草稿 | 仅保存，不产生版本 |
| 仅发布 | 生成不可变版本，不创建实例 |
| 发布并创建实例 | 生成版本后进入实例创建流程 |

弹窗显示变更摘要（与上一版本的节点/边/参数 Diff 概要）。确认后显示加载态，成功后跳转到模板详情或实例创建页。

### 3.4 版本详情（`/templates/:templateId/versions/:versionId`）

与模板详情共享 DAG 组件，但版本标签页固定为当前 versionId。增加"版本 Diff"入口（选择对比版本后展示 JSON Diff 和 DAG 变更高亮）。

## 4. DAG 画布

### 4.1 画布组件

使用 `@vue-flow/core`。底色 `#0d1117`（深色主题）或 `#f6f8fa`（浅色主题），24px 点阵网格。

工具栏（画布左上角浮动）：

| 图标 | 功能 |
|---|---|
| 🔍 + / − | 缩放 |
| ⤢ | 适应窗口 |
| ⇄ | 切换横向/纵向布局 |
| 🔄 | 自动布局 |
| ⛶ | 全屏 |
| 📷 | 导出图片 |

右上角浮动：Minimap。

### 4.2 节点卡

尺寸约 240 × 132px。内容：

```
┌─────────────────────────────────────┐
│ ● IN_PROGRESS  [类型徽标]           │
│ Task 名称                           │
│ Runtime: claude-code · Attempt #2   │
│ ⏱ 04:23 · 🔁 1 · ✅ 已通过 3/5 检查 │
│ ┌───────────────────────────────┐   │
│ │ 进入工作台 →                  │   │
│ └───────────────────────────────┘   │
└─────────────────────────────────────┘
```

状态颜色：

| 状态 | 边框色 | 连线 |
|---|---|---|
| PENDING / BLOCKED | 灰 | 灰虚线 |
| IN_PROGRESS | 青蓝 `#58a6ff` | 动态流动 |
| DONE | 绿 `#3fb950` | 绿实线 |
| FAILED | 玫红 `#f85149` | 红实线 |
| ABORTED | 灰 | 灰终止标 |
| WAITING_FOR_HUMAN | 紫 `#bc8cff` | 紫脉冲 |
| WAITING_FOR_CAPABILITY | 橙 `#d29922` | 橙告警 |
| UNROUTABLE | 橙 | 橙告警 |

状态同时通过颜色、图标和文字呈现，不依赖颜色区分。

### 4.3 校验面板

编辑模式下，DAG 下方或右侧显示校验结果列表：

```
校验结果：2 个错误
━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔴 E-001 · 节点 "deploy" · 存在自依赖
   位置：JSON 第 42 行
🔴 E-002 · 节点 "test" · 依赖的节点 "build" 不存在
   位置：JSON 第 58 行
```

点击错误行滚动到 JSON 对应位置并在 DAG 上高亮该节点。

## 5. 实例页面

### 5.1 实例列表（`/instances`）

与模板列表结构类似。表格列：实例名称 · 模板/版本 · 状态 · 进度（3/7 完成）· Git Commit · Workspace · 创建时间 · 操作。

状态筛选：全部 / QUEUED / RUNNING / PAUSED / DRAINING / 终态（SUCCEEDED/FAILED/ABORTED/SUPERSEDED/ARCHIVED）。

### 5.2 实例创建（`/templates/:templateId/instances/new`）

三步向导：

**Step 1 · 选择版本**：从可用版本列表选择（DISABLED/ARCHIVED 版本灰显不可选）。右侧展示该版本的只读 DAG 摘要。

**Step 2 · 配置实例**：

| 字段 | 说明 |
|---|---|
| 输入参数 | 根据 Template Parameter Schema 动态生成表单；敏感值用 Secret Reference |
| Git Branch/Commit | 下拉选择分支或输入 SHA；解析后显示不可变 commit SHA |
| Run Workspace | 按 ADR §5 隔离等级选择：ORIGINAL / GIT_WORKTREE / CONTROLLED_COPY / DEMO_TEMP |
| Execution Profile | 从项目组允许的 Profile 列表选择 |

**Step 3 · 确认**：参数校验摘要 · Git 解析结果 · Workspace 可用性 · 预期能力范围。底部操作：稍后启动 / 立即执行。

选择"立即执行"后直接进入实例详情页，并自动触发 Capability Preflight。

### 5.3 实例详情（`/instances/:instanceId`）

**顶栏**（56px）：面包屑（项目组 / 模板 / 版本 / 实例）· 实例状态徽标 · 进度 · 控制按钮。

控制按钮按权限和状态渲染：

| 操作 | 可用状态 | 角色 |
|---|---|---|
| 启动 | QUEUED | Editor+ |
| 暂停 | RUNNING | Editor+ |
| 恢复 | PAUSED | Editor+ |
| Drain | RUNNING | Editor+ |
| 中止 | RUNNING / PAUSED / DRAINING | Editor+ |
| 修改并重新执行 | RUNNING / PAUSED / 终态 | Editor+ |
| 删除 | QUEUED / 终态 | Editor+ |

危险操作（中止/删除/变更重执行）弹确认对话框。

**主体区域**（从上到下）：

1. **上下文摘要条**：参数 · Git SHA · Run Workspace（隔离等级徽标）· Execution Profile。
2. **运行 DAG**：节点状态由 Task/Attempt 实时驱动。节点点击进入节点工作台。
3. **Tab 栏**：Preflight · Manifest · 日志 · 证据 · ChangeSet · 时间线。

#### Run Workspace 展示（FR-INS-06）

上下文摘要条中的 Workspace 卡片：

```
┌─────────────────────────────────────────────┐
│ 📁 Run Workspace                             │
│ 名称: my-project-feature-x                   │
│ 隔离等级: GIT_WORKTREE                       │
│ 创建时间: 2026-10-01 10:00                   │
│ 保留策略: 7 天（终态后自动清理）              │
│ 状态: 🟢 ACTIVE / 🟡 RETAINED / 🔴 EXPIRED  │
│ [清理策略管理] (Admin only)                   │
└─────────────────────────────────────────────┘
```

清理前 24 小时显示黄色告警条："Run Workspace 将于 N 小时后自动清理。如需保留请联系项目组 Admin。"（Viewer 只读提示，Editor/Admin 可以延长保留）。

#### 产物保留展示（FR-CAP-11）

产物 Tab 中，每条产物显示保留到期时间。过期产物显示灰色删除线和"已清理"标记，不可下载但保留元数据记录。

### 5.4 Run 生命周期展示

根据 PRD 澄清：一个 Instance 只有一个 Run。实例详情顶栏不显示"Run 选择器"。Run 状态直接由 Instance 状态推导：

| Instance 状态 | Run 状态（推导） |
|---|---|
| QUEUED | PENDING |
| RUNNING | RUNNING |
| PAUSED | PAUSED |
| DRAINING | DRAINING |
| SUCCEEDED / FAILED / ABORTED / SUPERSEDED | TERMINAL |

实例详情不出现独立的"Run 详情页"。Run 级信息（Workspace、执行策略）在实例上下文摘要条中展示。

## 6. 执行能力与节点工作台

### 6.1 Capability Preflight 面板

实例详情 Preflight Tab 或实例创建 Step 3 展示：

```
能力预检：⚠ 1 项阻塞
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Agent Runtime: claude-code (ACP v1.2)
✅ Execution Profile: standard-dev
✅ Skill Bundle: tdd-workflow v1.3.0
❌ MCP Grant: filesystem (UNHEALTHY)
   └─ 健康检查失败：无法连接 MCP Server
   └─ [重试检查]  [查看 MCP 治理]
✅ Run Workspace: GIT_WORKTREE (已初始化)
✅ Budget: $5.00 / $10.00
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[重新预检全部]  [跳过并启动]  (Editor+ only)
```

阻塞项不可跳过（除非显式允许并记录审计）。阻塞时"启动"按钮灰色禁用并显示原因 tooltip。

### 6.2 Execution Manifest 展示

节点详情的 Manifest Tab 或专用路由 `/instances/:id/attempts/:attemptId/manifest`。

只读 JSON 视图（Monaco 只读模式），包含运行时、Skill、MCP、Workspace、策略版本和解析来源。顶部提供"复制摘要"和"审计跳转"按钮。

重试或重新执行后，新旧 Manifest 差异以行内 Diff 形式展示。

### 6.3 节点工作台（`/instances/:instanceId/tasks/:taskId`）

三栏布局（参考上版基线 §9）：

| 栏 | 宽度 | 内容 |
|---|---|---|
| Agent Collaboration | 36–42% | 对话 · Agent Update · Tool Call · 人工消息 |
| Workspace File Tree | 20–25% | 文件树 · Git 状态 · 变更标记 |
| Code Editor / Runtime | 35–44% | Monaco 编辑器 + Runtime Panel |

顶部状态条：返回按钮（始终可见）· 面包屑 · Task 状态 · Runtime · Attempt · 时长 · Capability 摘要徽标。

Capability 状态条（首屏）：

```
✅ Preflight 通过 · Manifest 已冻结 · Skill 1 · MCP 2 · Workspace: GIT_WORKTREE
```

阻塞时替换为橙色告警条，显示阻塞原因和修复入口。

## 7. 变更与审计

### 7.1 ChangeSet 面板

实例详情 ChangeSet Tab 展示：

```
ChangeSet CS-20261001-001
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
发起人: zhangsan · 2026-10-01 14:30
类型: Instance ChangeSet
影响节点: 3 / 7
  ├── 直接修改: task-deploy
  ├── 间接受影响: task-integration-test
  └── 需重跑: task-build, task-deploy, task-integration-test
可复用: task-lint, task-unit-test
产物复用依据: 输入参数和依赖未变化
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[确认执行] (Editor+)  [取消]
```

确认后创建 successor instance。旧实例状态变为 SUPERSEDED，保留完整审计历史和 successor 关系链接。

### 7.2 审计日志

实例详情时间线 Tab 或全局执行日志页。每条审计记录显示：

| 字段 | 示例 |
|---|---|
| 时间 | 2026-10-01 14:30:00 |
| Actor | zhangsan (Editor) |
| 动作 | ABORT_INSTANCE |
| 目标 | instance-inst-789 |
| 结果 | SUCCESS |
| 原因 | 上游 Git 仓库变更，需重新执行 |

审计记录不可编辑或删除。按动作类型筛选：发布 / 启动 / 暂停 / 恢复 / 中止 / 删除 / 变更 / 文件写入 / 能力变更。

### 7.3 权限矩阵管理（Admin）

全局设置 → 项目组 → 权限。展示当前三级角色到操作的可视化矩阵（复用 §2 表格）。Admin 可以查看但不能在 UI 中修改角色定义（角色固定，成员分配通过项目管理完成）。

### 7.4 Project Workspace 管理（Admin）

全局设置 → 项目组 → Project Workspaces。展示已登记的 Workspace 列表，每个卡片提供 Preflight 和删除操作。

- **登记**：Admin 填写名称、来源类型（本地目录 / Git Clone）、权限（读写 / 只读）和路径，确认后创建。
- **Preflight**：检查 Workspace 是否可读、可写、是否为 Git 仓库、是否有脏修改。
- **删除**：Admin 可以删除未被运行中实例锁定的 Workspace。点击删除后弹出确认对话框（"确认删除此 Workspace？删除后不可恢复。"），确认后调用 `DELETE /api/workspaces/:workspace_id` 并刷新列表。如果 Workspace 正在被使用，显示错误提示"Workspace 正在被运行中的实例使用，无法删除"。
- 已删除 Workspace 的历史 Run Workspace Binding 和审计记录保留不删除。

## 8. 全局横切面

### 8.1 主题与可访问性

- Light / Dark / System 三模式，语义 Token 驱动
- 状态同时用颜色、图标和文字
- WCAG 2.1 AA 对比度
- 核心路径支持键盘操作（Tab / Shift+Tab / Enter / Escape / 方向键）
- DAG 画布提供方向键节点导航和 Enter 进入工作台

### 8.2 响应式断点

沿用上版基线 §13 的三档布局（≥1440px / 1024–1439px / <1024px），不做修改。

### 8.3 空状态与加载态

| 场景 | 展示 |
|---|---|
| 模板列表为空 | 引导文案 + 创建按钮 |
| 实例列表为空 | "当前项目组还没有实例" + 创建入口 |
| DAG 校验中 | 画布半透明 + 加载指示器 |
| Preflight 进行中 | 面板 spinner + "正在检查 N 项能力" |
| API 不可用 | 离线横幅 + 最后快照 + 重试按钮 |
| Demo 模式 | 全局橙色横幅 "当前为演示模式，数据为模拟" |

### 8.4 错误展示

API 错误使用统一的 Toast 通知 + 内联错误卡片：

```
┌──────────────────────────────────────────┐
│ ❌ 保存草稿失败 (409)                     │
│ 草稿已被他人修改 (revision mismatch)     │
│ [查看冲突详情]  [刷新页面]               │
└──────────────────────────────────────────┘
```

### 8.5 SSE 实时更新

使用 SSE 推送实例状态、Task 状态、Attempt 变化和日志流。断线后通过 Last-Event-ID 续传；游标过期后显示"数据已过期"横幅并提供"重新加载"按钮，不静默刷新。
