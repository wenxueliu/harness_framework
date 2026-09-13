# Harness Dashboard 技术实施计划

> 状态：可执行技术方案
> 更新日期：2026-09-13
> 产品与交互基线：[`dashboard-ui-design.md`](dashboard-ui-design.md)
> 领域语言：[`../CONTEXT.md`](../CONTEXT.md)

## 1. 目的与实施边界

本文把已经确认的 Dashboard 产品设计落实为后端模型、状态机、API、权限、前端状态管理、迁移步骤和验收门槛。实现者可以按本文拆分 Issue 和提交，不需要再次决定 Project Group、Run Workspace、Attempt Workspace Binding 的基本语义。

首轮交付目标是：在不破坏现有 CLI、Consul/File/Local KVStore 和运行历史的前提下，把当前运行看板逐步升级为可观察、可介入、可浏览工作区并最终可安全编辑文件的操作台。

不在首轮范围内：

- 多租户 SaaS 计费和租户隔离；
- 浏览器任意 Shell；
- 自动合并隔离 Workspace 的修改；
- 文件重命名、移动、删除和批量替换；
- 500 个以上节点同时完整渲染；
- 用 Project Group 或 `service_name` 参与 Agent 路由。

## 2. 已锁定的技术决策

1. `Project Group` 是所有权和授权边界，不是调度对象。
2. `Run Workspace` 归属于 Run；每个 Task Attempt 保存不可变的 `Attempt Workspace Binding`。
3. 新 Dashboard 创建 Run 时先校验并准备 Workspace，再允许任务进入调度队列。
4. 新接口只接受 Workspace ID 和相对路径；生产环境不接受浏览器传入绝对路径。
5. `service_name` 仅为业务标签；兼容字段 `acp.cwd` 只在受控边界内解析。
6. 浏览器写操作采用乐观并发控制、幂等键和原子替换，绝不静默覆盖。
7. 服务端事件使用 SSE，命令继续使用 HTTP；SSE 必须支持断线续传和快照重置。
8. 前端以真实 API 为唯一执行事实源；Demo 数据只能在显式 Demo 模式出现。
9. 项目组及 Workspace 元数据继续通过 `KVStore` Protocol 保存，不能只对 Consul 实现。
10. Vue 前端增加 Pinia 管理跨路由实体状态，DAG 使用 `@vue-flow/core`，Monaco 动态加载。

## 3. 目标架构

```text
Browser (Vue 3)
├── HTTP commands / queries
└── SSE event stream
        │
Harness WebAPI
├── AuthenticationContext / AuthorizationService
├── ProjectGroupService
├── RunWorkspaceService
├── WorkspaceFileService
├── EventJournal
├── existing RunManager / AdaptiveControl / Session APIs
└── KVStore Protocol + controlled filesystem + Git
        │
ACPDispatcher
└── resolves cwd only from Attempt Workspace Binding
```

建议新增或拆分的 Python 模块：

| 模块 | 职责 |
|---|---|
| `auth.py` | 认证上下文、角色与 capability 计算、可信代理边界 |
| `project_groups.py` | Project Group、成员、Workflow 归属与引用 |
| `workspace_models.py` | Workspace 领域模型、序列化和状态校验 |
| `workspace_manager.py` | 登记、预检、worktree/快照创建、保留和清理 |
| `workspace_security.py` | 路径解析、敏感文件策略、大小和读写权限 |
| `workspace_files.py` | 树、文件、搜索、变更、乐观并发写入 |
| `event_journal.py` | 可重放事件日志、SSE 游标和快照重置 |
| `capabilities.py` | 部署能力和当前 actor 能力输出 |

现有 `run_manager.py`、`acp_dispatcher.py`、`webapi.py` 和 `daemon.py` 只承担编排与装配，不重复实现上述规则。

## 4. 领域数据模型

所有 ID 为服务端生成的不可变字符串；名称可以修改。时间统一保存为 UTC RFC 3339，API 响应同时提供明确时区的时间字符串。

### 4.1 ProjectGroup

```json
{
  "group_id": "grp_01...",
  "name": "Harness Core",
  "description": "...",
  "status": "ACTIVE",
  "default_workspace_id": "pws_01...",
  "policy": {
    "allow_browser_drafts": true,
    "workspace_retention_days": 7
  },
  "created_by": "user:alice",
  "created_at": "2026-09-13T03:00:00Z",
  "updated_at": "2026-09-13T03:00:00Z",
  "revision": 1
}
```

`status` 只允许 `ACTIVE -> ARCHIVED`。归档后拒绝创建 Workflow、Run、成员和 Workspace；已有 Run 可继续，历史只读。

### 4.2 ProjectWorkspace

```json
{
  "workspace_id": "pws_01...",
  "group_id": "grp_01...",
  "name": "harness-framework",
  "source_type": "LOCAL_PATH",
  "root_ref": "managed-root:harness-framework",
  "git_url": null,
  "default_ref": "main",
  "access": "READ_WRITE",
  "status": "READY",
  "policy": {
    "sensitive_patterns": [],
    "max_edit_bytes": 2097152,
    "max_preview_bytes": 10485760
  },
  "revision": 1
}
```

`source_type` 为 `LOCAL_PATH` 或 `GIT_CLONE`。持久化记录可以保存物理位置，但普通 API 只返回逻辑名称；仅诊断接口向 Owner/Maintainer 返回脱敏路径。

### 4.3 RunWorkspace

```json
{
  "run_workspace_id": "rws_01...",
  "req_id": "hello-001",
  "run_id": "run_01...",
  "project_workspace_id": "pws_01...",
  "strategy": "GIT_WORKTREE",
  "resolved_commit_sha": "40位提交SHA",
  "status": "READY",
  "read_write": true,
  "temporary_demo": false,
  "retention_until": "2026-09-20T03:00:00Z",
  "snapshot_manifest_id": null,
  "revision": 1
}
```

`strategy` 为 `ORIGINAL`、`GIT_WORKTREE`、`CONTROLLED_COPY` 或 `DEMO_TEMP`。branch/tag 只作为输入和展示信息，执行记录必须保存解析后的 commit SHA。

Workspace 状态机：

```text
PROVISIONING ──成功──> READY ──首个 Attempt──> ACTIVE
      │                  │                         │
      └──失败──> FAILED  └──Run 结束──────────────┤
                                                  v
                                    RETAINED 或 CLEANUP_PENDING
                                                  │
                                                  v
                                             TRASHED ──24h──> DELETED
```

有未合并 commit、未归档修改或 Artifact 的 Workspace 进入 `RETAINED`，人工解决后才能进入清理流程。

### 4.4 AttemptWorkspaceBinding

```json
{
  "binding_id": "awb_01...",
  "req_id": "hello-001",
  "run_id": "run_01...",
  "task_id": "implement",
  "attempt_id": "attempt_01...",
  "workspace_id": "rws_01...",
  "binding_type": "RUN_SHARED",
  "write_scope": ["src/**", "tests/**"],
  "base_commit_sha": "40位提交SHA",
  "writable": true,
  "bound_at": "2026-09-13T03:01:00Z"
}
```

绑定创建后不可修改。需要换 Workspace 时必须创建新 Attempt。`binding_type` 为 `RUN_SHARED` 或 `ISOLATED`；隔离结果只允许由显式 Merge Task 合并。

### 4.5 WorkspaceManifest

Manifest 保存历史重建所需的相对路径、文件类型、大小、hash、Git ref 和未提交 Patch 引用，不默认保存所有文件正文。含代码或秘密的 Patch 按项目组保留策略处理。

## 5. KVStore 键空间与一致性

沿用现有 KVStore 的 `get/put/delete` 和 CAS 能力。建议键空间：

```text
project-groups/<group_id>/record
project-groups/<group_id>/members/<subject_id>
project-groups/<group_id>/workflows/<req_id>
project-groups/<group_id>/references/<req_id>
project-groups/<group_id>/workspaces/<workspace_id>

workflows/<req_id>/project-group
workspaces/projects/<workspace_id>/record
workflows/<req_id>/runs/<run_id>/workspace/record
workflows/<req_id>/runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/workspace-binding

events/<stream_id>/<zero_padded_sequence>
events/<stream_id>/head
audit/<yyyy-mm>/<event_id>
```

必须保持以下不变量：

- 一个 Workflow 只有一个主 Project Group；跨组关系只能写入 `references`。
- 一个 Run 只有一个 Run Workspace；一个 Attempt 只有一个 Binding。
- 先使用 CAS 创建正向记录，再建立可重建的索引；索引写失败进入修复队列，不能创建第二个主记录。
- 所有可更新记录包含 `revision`，PATCH 必须携带 `expected_revision`。
- 列表接口不得依赖 Consul 专有事务；Local/FileStore 下的行为必须一致。
- 删除索引不能删除执行历史；归档代替业务对象硬删除。

如果现有 KVStore 缺少通用前缀扫描，先扩展为跨实现一致的 `kv_list(prefix, cursor, limit)`，并为三种存储实现契约测试。不要用 `list_services` 模拟领域对象查询。

## 6. Run 创建、兼容模式与调度

### 6.1 新 Dashboard 路径

`POST /api/workflows/:reqId/runs` 的顺序固定为：

1. 验证 actor 的 `run:create` capability 和 Project Group 状态；
2. 校验 Workspace 选择、Git ref、写权限、脏目录和并行写冲突；
3. 创建状态为 `PROVISIONING` 的 Run 和 Run Workspace；
4. 准备 worktree、受控副本或 Demo 临时目录；
5. 写入不可变 Git SHA、启动快照和审计事件；
6. 将 Workspace 置为 `READY`，Run 置为 `RUNNING`；
7. 发布可执行任务并触发现有调度。

请求缺少 Workspace 或选择无效时返回 `422 WORKSPACE_SELECTION_REQUIRED`，不创建 Run。已经创建 `PROVISIONING` 记录后准备失败，则保留失败记录，将 Run 与 Workspace 置为 `FAILED`，且不激活任何 Task。

Run 已运行后，如果绑定目录被外部删除、权限变化或挂载失效，当前 Attempt 进入 `WAITING_FOR_HUMAN`，原因使用 `WORKSPACE_UNAVAILABLE`。恢复时重新验证原绑定；不能把同一 Attempt 偷换到其他目录。

### 6.2 现有 CLI 兼容路径

`sync_to_consul.py --publish` 和已有自动分发行为在迁移期保留：

- 未启用 managed workspace 时，继续从受控 `acp.cwd`、`repo_path`、`--acp-workspace-root` 解析；
- local/demo 模式可以按 Run 创建 `/tmp/harness-<run-id>`，并标记 `temporary_demo=true`；
- production managed workspace 模式下，CLI 必须显式给出已登记 `workspace_id`，否则发布规范成功但不自动创建 Run；
- `service_name` 不加入任何 Workspace fallback。

兼容分支必须发出结构化弃用事件，Dashboard 可以显示迁移建议。待 CLI 支持显式 Run 创建后，再单独决定旧 fallback 的移除版本。

### 6.3 并行写规则

- 顺序 Task 默认共享 Run Workspace。
- 可写 Task 应声明 `write_scope`；未声明时按全 Workspace 写处理。
- 同时处于可执行状态且 scope 相交的 Task 不能共享可写 Workspace。
- 冲突时由计划选择串行化或创建隔离 Workspace，不允许运行时无提示竞争。
- scope 只用于调度预检，不代替真实文件权限和路径校验。

## 7. 认证与权限模型

### 7.1 身份来源

- 生产环境：OIDC 中间件或受信反向代理提供经过验证的 subject、display name 和 group claims。
- Harness 仅在请求来自配置的代理地址且签名/共享验证通过时读取身份头；必须丢弃公网客户端伪造的同名头。
- local 模式：daemon 显式配置 `local_user`，响应和页面持续显示 Local Mode。
- 请求正文中的 `actor` 永远不参与授权或审计身份生成。

### 7.2 Capability 矩阵

| Capability | Owner | Maintainer | Developer | Viewer |
|---|:---:|:---:|:---:|:---:|
| `group:read` | ✓ | ✓ | ✓ | ✓ |
| `group:manage` / `member:manage` / `group:archive` | ✓ |  |  |  |
| `workspace:register` / `workspace:policy` / `workspace:merge` | ✓ | ✓ |  |  |
| `workflow:draft` | ✓ | ✓ | ✓ |  |
| `workflow:publish` | ✓ | ✓ |  |  |
| `run:create` / `run:control` / `task:retry` | ✓ | ✓ |  |  |
| `task:message:queue` | ✓ | ✓ | ✓ |  |
| `task:message:interrupt` | ✓ | ✓ |  |  |
| `file:read` | ✓ | ✓ | ✓ | ✓ |
| `file:write` / `checkpoint:create` | ✓ | ✓ | ✓ |  |
| `workspace:path:diagnose` | ✓ | ✓ |  |  |

跨组引用默认只授予 `group:read` 范围内的 Workflow 读取能力；主项目组必须显式授予运行或介入 capability。平台策略、组策略、Workspace 策略和 Binding 权限逐层取交集，后层不能放宽前层。

## 8. HTTP API 契约

保留现有 `/api` 前缀和接口，新增接口使用复数资源名。所有写接口接受 `Idempotency-Key`；所有响应带 `request_id`。错误统一为：

```json
{
  "error": {
    "code": "WORKSPACE_SELECTION_REQUIRED",
    "message": "请选择本次运行使用的工作区",
    "details": {},
    "request_id": "req_http_01..."
  }
}
```

常用状态码：`400` 语法错误、`401` 未认证、`403` capability 不足、`404` 不存在或无权获知、`409` revision/文件冲突、`422` 领域校验失败、`429` 限流。

### 8.1 Project Group 和 Workspace

```text
GET    /api/project-groups?status=&cursor=&limit=
POST   /api/project-groups
GET    /api/project-groups/:groupId
PATCH  /api/project-groups/:groupId
GET    /api/project-groups/:groupId/members
PUT    /api/project-groups/:groupId/members/:subjectId
DELETE /api/project-groups/:groupId/members/:subjectId
GET    /api/project-groups/:groupId/workflows
POST   /api/project-groups/:groupId/workflow-references
DELETE /api/project-groups/:groupId/workflow-references/:reqId

GET    /api/project-groups/:groupId/workspaces
POST   /api/project-groups/:groupId/workspaces
GET    /api/workspaces/:workspaceId
PATCH  /api/workspaces/:workspaceId
POST   /api/workspaces/:workspaceId/preflight
```

登记本地目录时，浏览器提交管理员可见的 logical root alias 和相对目录，不直接传任意服务器路径。Git URL 必须经过协议、host allowlist 和凭据引用校验；凭据本身不进入 KV 记录。

### 8.2 Run 和 Binding

```text
POST /api/workflows/:reqId/runs
GET  /api/workflows/:reqId/runs/:runId/workspace
GET  /api/workflows/:reqId/tasks/:taskId/attempts
GET  /api/workflows/:reqId/tasks/:taskId/attempts/:attemptId/workspace-binding
```

Run 创建请求：

```json
{
  "workspace": {
    "project_workspace_id": "pws_01...",
    "strategy": "GIT_WORKTREE",
    "git_ref": "feature/dashboard",
    "accept_dirty": false
  },
  "save_as_workflow_default": false
}
```

成功返回 `201` 和 Run 快照；Workspace 准备时间较长时返回 `202`，客户端根据 `operation_id` 和 SSE 观察进度。相同幂等键必须返回同一个 Run。

### 8.3 文件、变更和 Tool Action

```text
GET  /api/workspaces/:workspaceId/tree?path=&cursor=&limit=
GET  /api/workspaces/:workspaceId/file?path=
PUT  /api/workspaces/:workspaceId/file
GET  /api/workspaces/:workspaceId/search?q=&cursor=&limit=
GET  /api/workspaces/:workspaceId/changes
POST /api/workspaces/:workspaceId/checkpoints
GET  /api/workspaces/:workspaceId/actions
POST /api/workspaces/:workspaceId/actions/:actionId
```

```text
GET  /api/workspaces/:workspaceId/diff?path=
GET  /api/workflows/:reqId/runs/:runId/workspace/manifest
POST /api/workflows/:reqId/runs/:runId/merge-tasks
GET  /api/workflows/:reqId/runs/:runId/merge-tasks/:mergeId
POST /api/workflows/:reqId/runs/:runId/merge-tasks/:mergeId/apply
```

所有接口还要从当前 Task/Attempt 上下文确认 Binding；仅知道 `workspace_id` 不能绕过授权。实现时可以通过必填查询参数或上下文 header 传递 `attempt_id`，服务端仍须查 KV Binding。

文件保存请求：

```json
{
  "attempt_id": "attempt_01...",
  "path": "src/main.py",
  "content": "...",
  "expected_sha256": "...",
  "reason": "修复输入校验"
}
```

保存成功返回新 `sha256` 和 `WORKSPACE_FILE_CHANGED` 事件 ID。hash 不一致返回 `409 FILE_VERSION_CONFLICT`，响应只包含 base/current hash 和安全元数据；客户端重新读取当前版本后生成 base/current/draft 三方 Diff。敏感文件被策略禁止时不能借冲突响应泄露正文。

Tool Action 只接收登记 schema 中的参数，服务端将 action ID 映射为固定 argv、cwd 策略、超时和环境变量 allowlist；禁止 shell 拼接。

### 8.4 Capabilities

`GET /api/capabilities?group_id=&req_id=&run_id=&attempt_id=` 返回部署能力和当前 actor 的有效 capability：

```json
{
  "features": {
    "project_groups": true,
    "workspace_browse": true,
    "workspace_write": false,
    "workspace_diff": true,
    "sse_events": true
  },
  "permissions": ["group:read", "file:read"],
  "mode": "production",
  "reasons": {"workspace_write": "历史 Attempt 只读"}
}
```

前端禁用态必须展示 `reasons`；前端判断只改善体验，服务端仍逐请求授权。

## 9. Workspace 文件安全实现

### 9.1 路径解析

1. API 拒绝绝对路径、NUL、空段和 `..` 段；
2. 从已授权 Binding 得到服务端 root；
3. 使用规范化相对路径并验证目标仍位于 root；
4. 逐级拒绝越界 symlink，关键读写优先使用目录 fd 和 `O_NOFOLLOW`；
5. 打开文件后再次校验 inode/realpath，降低检查与使用之间的竞态；
6. `.git`、`.env*`、私钥、凭据和平台策略命中的路径默认拒绝。

目录树必须限制深度、条数和总耗时，并使用游标分页。搜索仅在允许路径内进行，限制结果数，不能把命令字符串交给 Shell。

### 9.2 读取和写入

- UTF-8 文本不超过 2 MB 时可编辑；2–10 MB 只读；更大或二进制只返回元数据。
- 大小阈值可由平台、组和 Workspace 策略加严。
- 写入前后都验证 Binding 状态、capability、策略和 `expected_sha256`。
- 写入同目录临时文件，设置受控权限，`fsync` 后用 `os.replace` 原子替换；必要时同步目录。
- 幂等结果至少保存到请求有效期结束，重复请求不能重复产生审计动作。
- 文件保存和 Human Message 是两个独立事务；UI 负责展示部分成功。
- 外部编辑通过目录监控或定期 hash 扫描归因为 `external`，Agent Tool Call 归因为 `agent`，WebAPI 保存归因为 `human`。

## 10. SSE 事件协议

入口：`GET /api/events?group_id=&req_id=&run_id=&task_id=`，响应 `text/event-stream`。客户端通过 `Last-Event-ID` 续传。

统一事件包：

```json
{
  "event_id": "evt_01...",
  "sequence": 1042,
  "type": "TASK_STATUS_CHANGED",
  "occurred_at": "2026-09-13T03:05:00Z",
  "subject": {
    "group_id": "grp_01...",
    "req_id": "hello-001",
    "run_id": "run_01...",
    "task_id": "implement",
    "attempt_id": "attempt_01..."
  },
  "actor": {"type": "agent", "id": "agent_01..."},
  "data": {}
}
```

协议要求：

- 每 15–30 秒发送 heartbeat 注释，代理不得缓存响应；
- 同一 stream 的 sequence 严格递增，重复投递允许，前端按 event ID 幂等；
- 游标仍在保留窗口内时，从下一条重放；
- 游标过期时发送 `STREAM_RESET_REQUIRED`，随后关闭连接；前端重新拉取快照再连接；
- SSE 只传状态和有限日志片段，大体积正文、Diff、Artifact 通过 HTTP 获取；
- 事件先持久化再对客户端可见，不能只依赖进程内队列；
- 第一版可以轮询 KV journal 驱动 SSE，后续再替换通知机制，协议不变。

## 11. 前端实施架构

### 11.1 依赖与目录

增加并锁定与现有 Vue/Vite 兼容的 `pinia`、`@vue-flow/core` 和 Monaco Vue 适配层。Monaco 必须拆包动态加载。

```text
ui/src/
├── api/
│   ├── client.ts
│   ├── types.ts
│   └── errors.ts
├── stores/
│   ├── capability.ts
│   ├── projectGroups.ts
│   ├── workflows.ts
│   ├── runs.ts
│   ├── workbench.ts
│   └── eventStream.ts
├── layouts/AppShell.vue
├── views/
│   ├── WorkflowDashboard.vue
│   ├── TaskWorkbench.vue
│   └── ExecutionLogs.vue
└── components/dashboard/...
```

### 11.2 状态规则

- Router 保存 group/workflow/task/attempt，打开文件使用可编码的 query 参数。
- Store 只缓存服务端实体和显式浏览器草稿，不自行发明 Task/Run 状态。
- 首屏先拉 snapshot，再连接 SSE；事件只按 revision/sequence 合并更新。
- SSE 断线显示“正在重连”，不可把历史缓存显示为实时。
- API 不可用时只展示带时间戳的只读缓存；无缓存时展示错误空态。
- capability 同时控制路由、按钮和解释文本，但不能替代后端授权。
- Monaco 草稿 key 为 `subject/workspace_id/path/base_sha256`，仅用 session storage；登出、策略变化或 Workspace 归档时清理。
- “保存并排队说明”按顺序执行保存和发消息，并分别展示结果；不得假设跨 API 原子事务。

### 11.3 DAG 与性能

- Vue Flow 节点数据只保存展示快照，完整日志和文件不进入节点对象。
- 100 Task 基准下测量首次布局、平移、缩放、筛选和状态批量更新。
- 高频事件在 animation frame 内批处理；不可每条日志触发全图重新布局。
- Monaco、历史 Diff、Runtime 日志分页器均延迟加载。
- 状态必须有文字/图标替代颜色；键盘可选择节点和进入工作台。

## 12. 迁移与兼容策略

### 12.1 数据迁移

1. 启动时确保虚拟 `unassigned` Project Group 可读，但不允许修改其策略。
2. 首次访问旧 Workflow 时惰性建立其主组指针；提供离线校验/修复命令用于批量迁移。
3. 旧 Run 没有 Workspace 记录时生成 `LEGACY` 只读诊断视图；只有路径仍在允许根目录内时才允许继续浏览。
4. 旧 Task Attempt 没有 Binding 时不猜测跨机器路径；显示“历史记录缺少工作区绑定”。
5. 新写入采用双读兼容：先读新记录，缺失时读取旧字段；不要长期双写两套事实源。

### 12.2 API 和前端发布

- 先发布后端 capabilities，所有新 feature 默认 false；
- 再发布能识别禁用态的新前端；
- 逐项启用 Project Group、Workspace browse、write、SSE；
- 保留当前 Workflow/Run/Session/Control API，直到新页面覆盖对应测试；
- WorkflowBuilder 保留入口并标记 Prototype，不能伪装成已发布工作流。

每个 feature flag 必须支持关闭后回到真实只读页面，而不是 Mock 页面。

## 13. 分阶段开发计划

### Phase 0：基础契约

- 扩展 KVStore 前缀列表能力及三后端契约测试；
- 建立统一错误包、request ID、AuthenticationContext、AuthorizationService；
- 实现 `/api/capabilities`；
- 定义事件包和领域模型序列化。

退出门槛：Local、File、Consul 存储通过同一套测试；现有 API 回归通过；新前端可正确显示能力禁用原因。

### Phase 1：真实运行看板

- 引入 AppShell、语义主题、Router 和 Pinia；
- 使用 Vue Flow 替换当前 DAG 展示；
- 完整映射 Task/Run 状态和错误；
- 复用现有 Control、Session、Human Message API；
- 保持项目组、文件和编辑入口为 capability 禁用态。

退出门槛：真实 hello-world Run 可从 DAG 定位 Task、查看历史并执行已有人工操作；无静默 Mock；100 Task 性能达标。

### Phase 2：Project Group 与权限

- 实现 Project Group、成员、主归属、跨组引用、归档；
- 接入可信身份，落实 capability 矩阵；
- 实现 Project Workspace 登记和 preflight；
- 旧 Workflow 迁入未分组视图。

退出门槛：角色和跨组授权集成测试通过；归档组不能创建 Run；三种 KV 后端行为一致。

### Phase 3：Run Workspace 与节点工作台

- 改造 Run 创建为 Workspace-first 流程；
- 实现 ORIGINAL、GIT_WORKTREE、CONTROLLED_COPY 和 DEMO_TEMP；
- ACPDispatcher 只从 Attempt Workspace Binding 解析 cwd；
- 实现并行 write_scope 检测和隔离 Binding；
- 完成 Task Workbench、Attempt/Session 切换和 Runtime Panel。

退出门槛：Agent、文件诊断和历史记录解析到同一 Binding；无 Workspace 的新生产 Run 无法启动；运行中 Workspace 丢失会进入等待人工。

### Phase 4：文件浏览与只读编辑器

- 实现路径安全层、目录树、读取、搜索、changes 和 manifest；
- 动态加载 Monaco，支持多标签、只读历史和 Diff；
- 实现敏感文件、二进制和大文件策略；
- 显示 agent/human/external 文件事件。

退出门槛：路径穿越/symlink/竞态安全测试通过；移动端只读；历史 Attempt 不可写。

### Phase 5：人工编辑与受控动作

- 实现 expected hash、幂等和原子保存；
- 实现三方 Diff、草稿生命周期和保存后 queue/interrupt；
- 实现 checkpoint commit；
- 实现登记式格式化、测试和验证 Action。

退出门槛：并发修改必定返回 409；重复请求不重复写入；保存与消息的部分成功可恢复；不存在任意命令执行入口。

### Phase 6：实时事件与运维完备性

- EventJournal、SSE 重放、heartbeat 和 reset；
- 完成执行日志筛选、深链和导出；
- Workspace 保留、Trash、恢复和清理任务；
- 完成可访问性、性能、审计和故障演练。

退出门槛：断网重连不丢状态；过期游标正确重建快照；清理不会删除 RETAINED Workspace；审计可追溯所有危险动作。

## 14. 测试计划

### API 集成测试

- 身份头伪造和越权读取/写入；
- Run 创建成功、422 未选择、准备失败和重复幂等请求；
- Workspace 丢失进入 `WAITING_FOR_HUMAN`；
- 文件读写、409、敏感文件、历史只读；
- SSE 正常、重复、断线续传、游标过期和慢客户端。

API 测试通过真实 HTTP/ASGI 服务器验证上述契约；不要求额外的单元测试。

### 端到端测试

覆盖 Viewer 浏览、Developer 保存并 queue、Maintainer 创建/控制 Run、Owner 管理成员和归档；覆盖 DAG 到 Task 深链、刷新恢复、Attempt 切换、三方 Diff 与部分成功提示。

UI 自动化使用 Dashboard 的浏览器驱动测试，覆盖项目组、Run、Task Workbench、Attempt 切换、文件树、编辑冲突、Merge Task、日志和 SSE 重连提示。

### 安全与性能测试

- `..`、编码绕过、symlink swap、TOCTOU、Git 参数注入、超大目录和大文件；
- 100 Task、持续 SSE、长日志和 50k 文件树场景；
- SSE 慢消费者必须受限，不能拖垮事件生产者。

## 15. 可观测性与审计

结构化日志至少包含 `request_id`、actor subject、group ID、req/run/task/attempt ID、workspace ID、action、result 和 latency；不得记录文件正文、Token 或未脱敏物理路径。

建议指标：

- Run Workspace 准备耗时和失败原因；
- 活跃 SSE 连接、重连、积压和 reset 次数；
- Workspace 文件读写次数、409、403 和策略拒绝；
- 等待人工原因分布；
- retained/trash Workspace 数量和占用；
- DAG snapshot 和事件 API 延迟。

审计元数据长期保留；正文、Patch、Diff 和 Artifact 按项目组策略加密与过期。

## 16. 风险与回滚

| 风险 | 控制与回滚 |
|---|---|
| Run 创建改造破坏 CLI | feature flag 隔离 managed workspace；保留旧读取路径和回归测试 |
| KV 多记录写入出现半完成 | 主记录 CAS、可重建索引、修复任务和一致性检查命令 |
| 浏览器文件 API 扩大攻击面 | 默认关闭写能力；独立安全层；按阶段启用；快速关闭 `workspace_write` |
| SSE 压垮 stdlib WebAPI | 限制连接/缓冲；先验证并发模型；必要时把事件服务迁到 ASGI，保持协议不变 |
| 原目录并行执行互相覆盖 | 可写 Run 独占锁、write_scope 预检和隔离 Workspace |
| 历史 Workspace 被清理 | manifest、retention、RETAINED 门禁和 Trash 延迟删除 |

当前 `BaseHTTPRequestHandler` 适合兼容 API，但长连接、认证中间件和并发文件传输会明显增加复杂度。Phase 0 应做 SSE 并发验证；如果不能稳定满足连接和关闭语义，应在 Phase 6 前把 WebAPI 迁移到 ASGI。该迁移不改变本文 HTTP/SSE 契约。

## 17. 需要记录的 ADR

实施 Phase 0–3 时补充以下 ADR，记录不可逆边界，不重新讨论已确认的产品目标。汇总记录见 [`adr/001-dashboard-boundaries.md`](adr/001-dashboard-boundaries.md)：

1. Workspace-first Run 创建与旧 `--publish` 自动运行的兼容期限；
2. KVStore 多记录一致性和索引修复策略；
3. 可信代理认证边界；
4. SSE EventJournal 的保留、sequence 和 reset 语义；
5. 原目录、worktree、受控副本三种 Workspace 隔离等级。

## 18. 完成定义

目标方案完成必须同时满足：

- Project Group、Run Workspace、Attempt Workspace Binding 在 UI、API、存储和 Agent 执行中含义一致；
- 每个新生产 Run 都有明确 Workspace，ACP 不再从 `service_name` 猜目录；
- 用户能从任意 Task 查看 Attempt/Session 历史，并在有权限时 queue 或 interrupt；
- 文件树和编辑器只访问当前 Binding，冲突和敏感文件不会被静默覆盖或泄露；
- 所有写入、控制和人工介入都能审计到可信 actor；
- SSE 重连后与服务端 snapshot 收敛；
- 正常模式不出现 Mock 数据，禁用能力有明确原因；
- Local、File、Consul 三种存储和现有 CLI 兼容测试通过；
- 每个 Phase 的退出门槛、自动测试、运维文档和 feature flag 回滚路径齐备。

满足本文后，`dashboard-ui-design.md` 可以直接进入按 Phase 拆分和编码阶段；后续澄清应限制在具体实现细节或 ADR，不再阻塞整体开发。

## 19. 当前代码落地状态（2026-09-13）

本轮实现已经把上述契约接入现有 Harness，而不是另起一套演示数据源：

- Phase 0–2：Local/File/Consul 统一 `kv_list`、CAS 分页、结构化错误、可信身份、Capability 精细求交、Project Group、成员角色、跨组引用、归档、Workspace 登记和 Preflight 已落地。
- Phase 3：Workspace-first Run 创建、幂等、四种 Workspace 策略、Manifest、共享与隔离 Attempt Binding、ACP cwd 解析、Attempt/Session 切换、Runtime Panel、Workspace 丢失等待人工已落地。
- Phase 4–5：路径安全、目录树、读取、搜索、changes、Diff、Manifest、敏感文件和大文件策略、Monaco 多标签、expected hash/409、session 草稿、原子保存、Checkpoint、固定 Action 白名单、三方冲突处理和显式 Merge Task 已落地。
- Phase 6：EventJournal 保留窗口、SSE 过滤/心跳/游标重放/reset、ASGI 入口、执行日志筛选/导出、Workspace RETAINED/CLEANUP_PENDING/Trash/恢复/后台清理 Worker、Agent/Human/External 文件事件和审计已落地。
- 前端真实 API 是唯一事实源；只有显式 `VITE_DEMO_MODE=true` 才加载 Demo fixture。项目组、工作区、Run 创建、Task Workbench、文件编辑、Merge Task 和执行日志都有真实路由。

验证方式限定为 API 集成测试和 UI 自动化测试；不新增单元测试。API 测试覆盖真实 HTTP/ASGI 契约，UI 测试覆盖 Dashboard 关键路径、Attempt 切换、文件编辑冲突和日志导航。
