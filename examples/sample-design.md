# 用户认证功能 - 设计文档

**设计ID:** DESIGN-20260502-001
**关联需求:** REQ-20260502-001

## 1. 设计概述

实现完整的用户认证体系：注册、登录、令牌刷新、登出。
涉及 3 个服务: user-service (核心认证逻辑)、gateway (JWT 验证中间件)、platform (E2E 测试)。

## 2. 任务分解

<!-- task:design:platform -->
### API 契约设计
- 依赖: []
- 描述: 设计用户认证相关 API 契约（注册/登录/刷新令牌/登出），输出 OpenAPI 3.0 规范
- 预估: 1h
- 审查: logic

<!-- task:backend:user-service -->
### 实现数据模型和迁移脚本
- 依赖: [API 契约设计]
- 描述: 创建 users 表，含字段: id, email, password_hash, status, created_at, updated_at
- 预估: 0.5h
- 审查: logic

<!-- task:backend:user-service -->
### 实现用户注册 API
- 依赖: [实现数据模型和迁移脚本]
- 描述: 实现 POST /api/v1/auth/register，含邮箱格式校验、密码强度策略（8+字符，大小写+数字）
- 预估: 2h
- 审查: security, logic

<!-- task:backend:user-service -->
### 实现用户登录 API
- 依赖: [实现数据模型和迁移脚本]
- 描述: 实现 POST /api/v1/auth/login，JWT 双令牌机制（access 15min + refresh 7d）
- 预估: 1.5h
- 审查: security

<!-- task:backend:user-service -->
### 实现令牌刷新和登出 API
- 依赖: [实现用户登录 API]
- 描述: 实现 POST /api/v1/auth/refresh 和 POST /api/v1/auth/logout，含 refresh token rotation
- 预估: 1h
- 审查: security, logic

<!-- task:backend:gateway -->
### 网关 JWT 认证中间件
- 依赖: [API 契约设计]
- 描述: 在 API Gateway 中实现 JWT Bearer Token 验证中间件，含限流（100 req/min per user）
- 预估: 1.5h
- 审查: security, performance

<!-- task:test:platform -->
### E2E 认证流程测试
- 依赖: [实现用户注册 API, 实现用户登录 API, 实现令牌刷新和登出 API, 网关 JWT 认证中间件]
- 描述: 端到端测试：注册→登录→访问受保护资源→刷新令牌→登出→再次访问被拒绝
- 预估: 1h
