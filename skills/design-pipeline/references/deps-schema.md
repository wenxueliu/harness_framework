# dependencies.json Schema

Harness Framework Aggregator 消费的任务依赖图格式。

## 顶层结构

平铺 dict，key 为任务名（kebab-case，≤ 64 字符），value 为任务定义。

```json
{
  "task-name-1": { "type": "...", "depends_on": [...], ... },
  "task-name-2": { "type": "...", "depends_on": [...], ... }
}
```

## 任务定义字段

| 字段 | 必需 | 类型 | 说明 |
|------|------|------|------|
| `type` | ✅ | string | `design` / `review` / `backend` / `test` / `deploy` / `parallel` / `aggregate` |
| `depends_on` | ✅ | array[string] | 上游任务名列表。`parallel` 和 `aggregate` 也必须声明（即使为空数组） |
| `acp` | 可选 | object | 指定 Claude/Codex、cwd、权限与 session；缺省按类型路由 |
| `agent_name` | 可选 | string | 旧注册/抢占 Worker 的兼容逻辑名称 |
| `service_name` | 可选 | string | 业务或仓库归属，仅作为上下文，不参与调度匹配 |
| `description` | 推荐 | string | 任务描述 |
| `capability` | 推荐 | string | `design` / `dev` / `test` / `review` / `deploy`，Agent 能力匹配 |
| `blocking` | 可选 | bool | 默认 `true`。`false` 表示非阻塞依赖（CONTRACT 类型） |
| `children` | parallel 必填 | array[string] | `parallel` 节点的子任务名列表 |
| `priority` | 可选 | int | 任务优先级，默认 0，越大越优先 |
| `metadata` | 可选 | object | 透传元数据（estimated_hours, test_bindings, review_requirements, design_doc 等） |

## 节点类型详解

### task — 普通任务

```json
{
  "hw-001": {
    "type": "backend",
    "depends_on": ["design-api"],
    "acp": {"agent": "codex"},
    "service_name": "user-service",
    "description": "实现用户注册 API",
    "capability": "dev",
    "blocking": true,
    "metadata": {
      "estimated_hours": 2,
      "test_bindings": {"ut_cases": ["UT-1"], "api_cases": ["API-1"]},
      "review_requirements": [{"reviewer": "security", "reason": "涉及认证"}]
    }
  }
}
```

### parallel — 并行扇出

所有依赖满足时，将所有 `children` 激活为 PENDING，自身标记为 DONE。

```json
{
  "wave-1": {
    "type": "parallel",
    "depends_on": [],
    "children": ["hw-001", "hw-002", "hw-003"]
  }
}
```

### aggregate — 聚合扇入

所有 `depends_on`（通常是 `parallel` 节点）DONE 时，自身标记 DONE，激活下游。

```json
{
  "wave-1-merge": {
    "type": "aggregate",
    "depends_on": ["wave-1"]
  }
}
```

## 依赖规则

1. **叶子任务初始 PENDING**，有依赖的任务初始 BLOCKED
2. **所有上游 DONE** → Aggregator 将下游设为 PENDING
3. **blocking: false** → 依赖不阻塞，任务可与其他任务并行激活
4. **循环依赖非法** — 在构建阶段就应检测并拒绝

## 验证规则

- `depends_on` 中引用的任务名必须存在于 deps 的 key 中
- `children` 中引用的任务名必须存在于 deps 的 key 中
- `parallel` 节点必须有 `children` 数组
- `acp.agent` 如提供，只能为 `claude` 或 `codex`
- 不能有指向自身的依赖
