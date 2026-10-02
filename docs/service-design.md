# Harness Orchestration 服务设计

> 版本：v1.0
> 状态：与 PRD v1.1 + UI/UX v2.0 对齐的实现级技术设计
> 更新日期：2026-10-02
> PRD：[`product-requirements.md`](product-requirements.md)
> UI/UX：[`ui-ux-design.md`](ui-ux-design.md)
> 范围：缺口分析中 P1 + P2 后端未实现功能

## 1. 数据模型

### 1.1 Execution Profile

```python
class ExecutionProfile:
    profile_id: str          # 不可变 ID
    group_id: str            # 所属项目组
    name: str
    version: str             # 语义化版本 "1.0.0"
    agent_runtime_id: str    # 引用 AgentRuntime
    workspace_policy: WorkspacePolicy
    resource_budget: ResourceBudget
    network_policy: NetworkPolicy
    secret_policy: SecretPolicy
    skill_bundle_ids: list[str]
    mcp_grant_ids: list[str]
    status: Literal["ACTIVE", "DISABLED"]
    created_by: str
    created_at: str
```

**存储**: KVStore `execution_profiles/{group_id}/{profile_id}`
**约束**: 项目组 Admin 创建/禁用；Editor 可在实例创建时选择但不修改定义。

### 1.2 Agent Runtime

```python
class AgentRuntime:
    runtime_id: str          # 不可变 ID
    name: str                # "claude-code", "codex"
    version: str
    adapter_id: str          # ACP adapter 引用
    acp_version: str         # "1.2"
    supported_capabilities: frozenset[str]  # "skill:inject", "mcp:inject", "file:write"
    health_endpoint: str
    status: Literal["HEALTHY", "UNHEALTHY", "OFFLINE"]
    registered_by: str
    registered_at: str
```

**存储**: KVStore `agent_runtimes/{runtime_id}`
**约束**: 平台 Admin 注册；health 定期轮询（60s），结果写入 KV。

### 1.3 Skill Bundle

```python
class SkillBundle:
    bundle_id: str           # 不可变 ID
    name: str
    version: str
    source: Literal["platform", "group", "external"]
    source_ref: str          # 仓库 URL 或平台路径
    content_hash: str        # SHA-256
    injection_method: Literal["workspace_rules", "project_config", "agent_provider"]
    schema: dict             # 注入参数 schema
    uploaded_by: str
    uploaded_at: str
```

**存储**: KVStore `skill_bundles/{bundle_id}`（版本不可变，新版本新 ID）
**约束**: 内容不作为秘密写入日志；注入方式由 Adapter 在预检阶段声明兼容性。

### 1.4 MCP Server / Grant

```python
class MCPServer:
    server_id: str
    name: str
    version: str
    transport: Literal["stdio", "sse", "streamable-http"]
    endpoint: str | None     # stdio 为 command
    health_endpoint: str | None
    tool_names: list[str]
    registered_by: str
    registered_at: str

class MCPGrant:
    grant_id: str
    server_id: str
    group_id: str
    profile_id: str
    expose_tools: list[str]  # 允许 Agent 看到的工具
    invoke_tools: list[str]  # 允许 Agent 调用的工具
    status: Literal["ACTIVE", "REVOKED"]
    granted_by: str
    granted_at: str
```

**存储**: KVStore `mcp_servers/{server_id}`, `mcp_grants/{group_id}/{grant_id}`
**约束**: `expose_tools` 和 `invoke_tools` 分离；未授权工具不进入 Agent 会话（FR-CAP-04）。

### 1.5 Execution Manifest

```python
class ExecutionManifest:
    manifest_id: str         # Attempt 级不可变快照
    attempt_id: str
    agent_runtime: dict      # AgentRuntime 序列化快照
    execution_profile: dict  # ExecutionProfile 序列化快照
    skill_bundles: list[dict]
    mcp_grants: list[dict]
    workspace_binding: dict  # AttemptWorkspaceBinding 快照
    secret_refs: list[str]
    policy_versions: dict    # {"profile": "1.0.0", "runtime": "1.2.0", ...}
    resolved_at: str
    resolved_by: Literal["system", "manual_retry"]
    previous_manifest_id: str | None  # 重试时引用
```

**存储**: KVStore `attempts/{attempt_id}/manifest`
**约束**: 生成后不可变（FR-CAP-07）。重试生成新 Manifest，`previous_manifest_id` 链接旧版。

### 1.6 Artifact

```python
class Artifact:
    artifact_id: str
    attempt_id: str
    instance_id: str
    type: Literal["log", "file", "evidence"]
    path: str                # 存储相对路径
    size_bytes: int
    sha256: str
    mime_type: str | None
    created_at: str
    retention_until: str     # 终态 + N 天
    status: Literal["ACTIVE", "EXPIRED", "PURGED"]
```

**存储**: KVStore `artifacts/{instance_id}/{artifact_id}`
**约束**: 终态后保留 N 天（N = Workspace 保留天数，FR-CAP-11）；到期后 status → EXPIRED，元数据保留，文件删除。

## 2. API 契约

### 2.1 Execution Profile

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| GET | `/api/execution-profiles?group_id=` | 全部 | 项目组下的 Profile 列表 |
| POST | `/api/execution-profiles` | Admin | 创建 Profile |
| PATCH | `/api/execution-profiles/:id` | Admin | 更新（乐观锁 revision） |
| DELETE | `/api/execution-profiles/:id` | Admin | 软删除（status → DISABLED） |

### 2.2 Agent Runtime

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| GET | `/api/agent-runtimes` | 全部 | 注册的 Runtime 列表 |
| POST | `/api/agent-runtimes` | Admin | 注册 Runtime |
| GET | `/api/agent-runtimes/:id/health` | 全部 | 查询健康状态 |

### 2.3 Skill Bundle

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| GET | `/api/skill-bundles?group_id=` | 全部 | 项目组可用 Bundle |
| POST | `/api/skill-bundles` | Admin | 上传/注册 Bundle |
| GET | `/api/skill-bundles/:id/content` | Editor+ | 获取 Bundle 内容（非秘密） |

### 2.4 MCP Server / Grant

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| GET | `/api/mcp-servers` | 全部 | 注册的 MCP Server |
| POST | `/api/mcp-servers` | Admin | 注册 MCP Server |
| GET | `/api/mcp-grants?group_id=` | 全部 | 项目组的 Grant 列表 |
| POST | `/api/mcp-grants` | Admin | 创建 Grant（指定 server + profile） |
| PATCH | `/api/mcp-grants/:id` | Admin | 修改 expose/invoke 工具列表 |
| DELETE | `/api/mcp-grants/:id` | Admin | 撤销 Grant |

### 2.5 Capability Preflight

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| POST | `/api/instances/:id/preflight` | Editor+ | 触发全量预检，返回逐项结果 |
| GET | `/api/instances/:id/preflight/latest` | 全部 | 获取最近一次预检结果 |

**Preflight 响应**:

```json
{
  "instance_id": "inst-789",
  "status": "BLOCKED",
  "checks": [
    {"check_id": "agent-runtime", "status": "PASS", "detail": "claude-code v1.2 HEALTHY"},
    {"check_id": "execution-profile", "status": "PASS", "detail": "standard-dev v1.0.0"},
    {"check_id": "skill-bundle", "status": "PASS", "detail": "tdd-workflow v1.3.0 (workspace_rules)"},
    {"check_id": "mcp-grant", "status": "FAIL", "detail": "filesystem UNHEALTHY", "reason": "无法连接 MCP Server", "remediation": "检查 MCP Server 端点或联系 Admin"},
    {"check_id": "workspace", "status": "PASS", "detail": "GIT_WORKTREE 已初始化"},
    {"check_id": "budget", "status": "PASS", "detail": "$5.00 / $10.00"}
  ],
  "manifest_id": null,
  "checked_at": "2026-10-02T10:00:00Z"
}
```

### 2.6 Execution Manifest

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| GET | `/api/attempts/:id/manifest` | 全部 | 获取 Manifest 快照 |
| GET | `/api/attempts/:id/manifest/diff` | 全部 | 与前次 Attempt 的 Manifest Diff |

### 2.7 Artifact

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| GET | `/api/instances/:id/artifacts` | 全部 | 实例产物列表（含 retention_until） |
| GET | `/api/artifacts/:id/download` | 全部 | 下载（EXPIRED 时返回 410 Gone） |

### 2.8 角色 API

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| GET | `/api/project-groups/:id/permission-matrix` | 全部 | 返回三级角色到操作的映射 |

**响应**:

```json
{
  "roles": {
    "ADMIN": ["group:manage", "member:manage", "workspace:policy", "profile:manage", "mcp:manage", "runtime:manage", "...editor_capabilities"],
    "EDITOR": ["workflow:draft", "workflow:publish", "instance:create", "instance:control", "changeset:create", "file:read", "file:write", "message:queue", "message:interrupt"],
    "VIEWER": ["group:read", "workflow:read", "instance:read", "log:read", "artifact:read", "manifest:read"]
  },
  "members": [...]
}
```

## 3. 状态机

### 3.1 Preflight 状态机

```
IDLE → CHECKING → PASSED → MANIFEST_GENERATED
                → BLOCKED → (用户修复) → CHECKING
```

- `PASSED` → 自动生成 Execution Manifest 并冻结
- `BLOCKED` → 不可启动；显示阻塞原因和修复入口
- 重新预检覆盖上次结果，但已生成的 Manifest 不受影响

### 3.2 Artifact 生命周期

```
ACTIVE → (终态 + N 天到期) → EXPIRED → (清理 worker) → PURGED
```

- `EXPIRED`: 元数据可查，文件删除，下载返回 410
- `PURGED`: 元数据也删除（仅保留审计事件）

## 4. 角色迁移

### 4.1 现有 → 目标

| 现有（auth.py） | 目标（PRD） | 能力映射 |
|---|---|---|
| OWNER | **Admin** | 现有 OWNER 能力 + `profile:manage` + `mcp:manage` + `runtime:manage` |
| MAINTAINER | **Editor** | 现有 MAINTAINER 能力 + `changeset:create` + `instance:create` |
| DEVELOPER | **Editor**（合并） | 现有 DEVELOPER 能力合并到 Editor |
| VIEWER | **Viewer** | 现有 VIEWER 能力 + `manifest:read` + `artifact:read` |

### 4.2 迁移策略

1. `Role` 枚举改为 `ADMIN / EDITOR / VIEWER`
2. `ROLE_CAPABILITIES` 按上表合并
3. KV 中已有 `role` 字段值映射：`OWNER→ADMIN`, `MAINTAINER→EDITOR`, `DEVELOPER→EDITOR`, `VIEWER→VIEWER`
4. 读取时兼容旧值，写入时使用新值（渐进迁移，不批量重写 KV）

## 5. Preflight 执行流程

```python
def run_preflight(instance_id: str) -> PreflightResult:
    instance = load_instance(instance_id)
    template_version = load_version(instance.version_id)
    profile = load_profile(instance.execution_profile_id)

    checks = []
    checks.append(check_agent_runtime(profile.agent_runtime_id))
    checks.append(check_execution_profile(profile))
    checks.append(check_skill_bundles(profile.skill_bundle_ids))
    checks.append(check_mcp_grants(profile.mcp_grant_ids))
    checks.append(check_workspace(instance.run_workspace_id))
    checks.append(check_budget(profile.resource_budget, instance))

    if all(c.status == "PASS" for c in checks):
        manifest = generate_manifest(instance, profile, checks)
        freeze_manifest(manifest)
        return PreflightResult(status="PASSED", manifest_id=manifest.manifest_id, checks=checks)
    return PreflightResult(status="BLOCKED", manifest_id=None, checks=checks)
```

预检在实例启动前和 Task Attempt 启动前各执行一次。Attempt 级预检失败进入 `WAITING_FOR_CAPABILITY`，不伪装为普通执行失败（FR-CAP-08）。

## 6. 单元测试规格

| 测试 ID | 覆盖 | 断言 |
|---|---|---|
| UT-PROFILE-01 | 创建 Profile | 返回 profile_id，KV 中存在记录 |
| UT-PROFILE-02 | 非 Admin 创建 Profile | 403 |
| UT-PROFILE-03 | 更新 Profile revision 冲突 | 409 |
| UT-RUNTIME-01 | 注册 Runtime | 返回 runtime_id，初始 status OFFLINE |
| UT-RUNTIME-02 | Health 更新 | status 从 OFFLINE → HEALTHY |
| UT-SKILL-01 | 上传 Bundle | content_hash 非空，version 不可变 |
| UT-MCP-01 | 创建 Grant | expose ≠ invoke 时通过 |
| UT-MCP-02 | Grant 撤销后预检 | mcp-grant check 返回 FAIL |
| UT-MANIFEST-01 | 预检通过后生成 Manifest | manifest_id 非空，不可修改 |
| UT-MANIFEST-02 | Manifest Diff | 新旧差异行正确 |
| UT-ARTIFACT-01 | 终态后 N 天到期 | status → EXPIRED，下载 410 |
| UT-ARTIFACT-02 | 清理后 | status → PURGED，元数据删除 |
| UT-ROLE-01 | 旧角色 OWNER 读取 | 映射为 ADMIN |
| UT-ROLE-02 | Editor 无法访问 Admin API | 403 |
| UT-PREFLIGHT-01 | 全部通过 | status=PASSED，manifest 已生成 |
| UT-PREFLIGHT-02 | MCP 不健康 | status=BLOCKED，阻塞项有 remediation |

## 7. API 集成测试规格

| 测试 ID | 场景 | 断言 |
|---|---|---|
| IT-01 | 创建 Profile → 创建实例 → 启动预检 → 启动实例 | 全链路 PASS |
| IT-02 | MCP Server 下线 → 预检 BLOCKED | 不可启动，显示修复建议 |
| IT-03 | MCP Server 恢复 → 重新预检 → PASSED | Manifest 生成 |
| IT-04 | 实例终态 → 等 N 天 → 清理 worker | Artifact EXPIRED，Workspace 清理 |
| IT-05 | Owner 尝试 Admin 操作 | 旧角色映射后通过 |
| IT-06 | Viewer 访问 Manifest 只读 | 200 OK |
| IT-07 | Editor 修改 Profile | 403 |
