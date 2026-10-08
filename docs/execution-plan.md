# Execution Plan — Harness Orchestration 缺口实现

> 来源: docs/tasks.yaml · docs/service-design.md · docs/ui-ux-design.md
> 服务: harness_framework (Python + Vue 3)
> 日期: 2026-10-03

## 1. 目标

按 5 个 Wave 实现 PRD 缺口分析中的全部 P1 + P2 功能：角色迁移、能力模型、Preflight、Manifest、Artifact 保留，以及对应前端页面。

## 2. TODO

- [ ] TASK-001: 角色模型迁移（四级→三级）
- [ ] TASK-002: Execution Profile 模型与 CRUD API
- [ ] TASK-003: Agent Runtime 注册与健康检查
- [ ] TASK-004: Skill Bundle 模型与上传 API
- [ ] TASK-005: MCP Server/Grant 模型与管理 API
- [ ] TASK-006: Artifact 模型与保留清理
- [ ] TASK-007: Capability Preflight 编排器
- [ ] TASK-008: Execution Manifest 模型、生成与 Diff
- [ ] TASK-009: 前端模板详情（版本选择 + Diff）
- [ ] TASK-010: 前端草稿冲突弹窗 + 发布确认弹窗
- [ ] TASK-011: 前端 Preflight 面板 + Manifest 展示
- [ ] TASK-012: 前端实例创建向导
- [ ] TASK-013: 前端 ChangeSet 影响分析 + 权限矩阵
- [ ] TASK-014: 前端 Workspace 保留告警 + 产物展示
- [ ] TASK-015: 端到端集成测试

## 3. 依赖与波次

| Wave | 并行 | 任务 | 阻塞关系 |
|---|---|---|---|
| 1 | 6 | 001-006 | 无 |
| 2 | 4 | 007-010 | 007←002,003,004,005 · 008←002,003,004,005 · 009←001 · 010←001 |
| 3 | 3 | 011-013 | 011←007,008 · 012←002,006 · 013←001 |
| 4 | 1 | 014 | ←006,012 |
| 5 | 1 | 015 | ←007,008,006,011,012 |

## 4. QA 场景

每任务在实现完成后运行对应 UT；最终 Wave 运行 IT-01~07。

```bash
cd /home/chengnanfeng/code/harness/services/harness_framework
python -m pytest tests/test_auth.py tests/test_execution_profiles.py tests/test_agent_runtimes.py tests/test_skill_bundles.py tests/test_mcp_grants.py tests/test_artifacts.py -v
```

## 5. 最终验证

- [ ] 全部 UT 通过
- [ ] 全部 IT 通过
- [ ] `npm run check` 通过（前端）
- [ ] 无新增 lint 错误
