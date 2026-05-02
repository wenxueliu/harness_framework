# 设计文档中的任务标记语法

当设计文档需要被 `design_to_deps.py` 自动解析时，使用以下结构化标记。

## 基本格式

每个任务由一个 `<!-- task:type:service_name -->` HTML 注释标记，后跟任务详情。

```
<!-- task:类型:服务名 -->
### 任务标题
- 依赖: [上游任务1, 上游任务2]
- 描述: 一句话描述
- 预估: Nh
- 审查: reviewer1, reviewer2
- 能力: capability_override
```

## 类型缩写

| 类型标记 | 映射到 type | 默认 capability |
|---------|------------|----------------|
| `backend` | `backend` | `dev` |
| `design` | `design` | `design` |
| `review` | `review` | `review` |
| `test` | `test` | `test` |
| `deploy` | `deploy` | `deploy` |

## 服务名

- 具体服务名: `user-service`, `order-service`, `gateway`
- `platform` — 平台级/跨服务任务
- `_test` — 纯测试任务

## 依赖

- `[]` 或 `- 依赖: []` — 无依赖，叶子任务
- `- 依赖: [任务标题A]` — 通过任务标题引用（会自动转换为 task name slug）
- `- 依赖: [任务标题A, 任务标题B]` — 多个依赖

依赖引用的是**任务标题**（`###` 后的文字），不是任务 ID。转换时自动 slugify 匹配。

## 完整示例

```markdown
## 任务分解

<!-- task:design:platform -->
### API 契约设计
- 依赖: []
- 描述: 设计用户认证相关 API 契约（注册/登录/刷新令牌/登出）
- 预估: 1h
- 审查: logic

<!-- task:backend:user-service -->
### 实现用户注册 API
- 依赖: [API 契约设计]
- 描述: 实现 POST /api/v1/auth/register，含邮箱校验和密码策略
- 预估: 2h
- 审查: security, logic

<!-- task:backend:user-service -->
### 实现用户登录 API
- 依赖: [API 契约设计]
- 描述: 实现 POST /api/v1/auth/login，JWT 双令牌机制
- 预估: 1.5h
- 审查: security

<!-- task:backend:gateway -->
### 网关认证中间件
- 依赖: [API 契约设计]
- 描述: 在 API Gateway 中实现 JWT 验证中间件和限流
- 预估: 1.5h
- 审查: security, performance

<!-- task:test:platform -->
### E2E 认证流程测试
- 依赖: [实现用户注册 API, 实现用户登录 API, 网关认证中间件]
- 描述: 端到端测试：注册→登录→访问受保护资源→刷新令牌→登出
- 预估: 1h
```

解析结果将生成:
- 3 个并行后端任务（都依赖 API 契约设计）
- 1 个 E2E 任务（依赖所有后端任务）
