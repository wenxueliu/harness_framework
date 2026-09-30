# Harness Framework 文档索引

> 文档入口：产品需求 → 用户旅程 → 领域模型 → 实现设计 → 架构与运行 → 测试与运维  
> 更新日期：2026-09-21

## 1. 推荐阅读顺序

新成员或需要理解作业流模板/实例闭环时，建议按以下顺序阅读：

1. [产品需求文档](product-requirements.md)
2. [用户旅程图](user-journey-map.md)
3. [领域模型与术语](../CONTEXT.md)
4. [作业流模板与实例需求](job-flow-template-instance-requirements.md)
5. [作业流模板与实例实现设计](../DESIGN.md)
6. [系统架构](architecture.md)
7. [ACP 执行与能力装配](acp-execution.md)
8. [Dashboard UI 设计](dashboard-ui-design.md)

文档职责遵循以下优先级：

1. CONTEXT.md：统一术语和领域边界；
2. docs/product-requirements.md：当前产品范围、用户旅程、功能需求、能力模型和缺口分级；
3. docs/user-journey-map.md：从模板创建到实例执行、变更、删除的页面旅程和优化优先级；
4. docs/job-flow-template-instance-requirements.md：模板、版本、实例、DAG、变更和验收细则；
5. DESIGN.md：当前实现方案、API、Capability Plane、ACP Gateway、数据布局和实施门槛；
6. 其他 docs/ 文档：对应模块的架构、运行机制和操作说明。

如果旧文档仍使用 Workflow / Requirement / Plan 作为产品主对象，应以新的 Workflow Template / Template Version / Workflow Instance 模型为准；旧文档只在对应技术模块仍有效时作为补充参考。

## 2. 当前产品与实现基线

| 文档 | 用途 | 状态 |
|---|---|---|
| [产品需求文档](product-requirements.md) | 当前人工编排产品基线、能力预检、用户旅程、P0/P1/P2 缺口 | 当前基线 |
| [用户旅程图](user-journey-map.md) | 模板、版本、实例、执行、变更和删除的端到端旅程 | 当前基线 |
| [领域模型与术语](../CONTEXT.md) | Template、Version、Instance、Run、Task、DAG 等术语定义 | 当前基线 |
| [模板与实例详细需求](job-flow-template-instance-requirements.md) | 业务规则、状态、删除、变更重执行、页面和验收 | 当前基线 |
| [实现设计](../DESIGN.md) | 后端、前端、API、存储、路由和实施计划 | 当前实现设计 |
| [系统架构](architecture.md) | Control/Scheduler/Capability/Execution/Observation 五平面架构 | 当前架构 |
| [ACP 执行与能力装配](acp-execution.md) | ACP Adapter、Skill、MCP、Manifest 和恢复 | 当前架构 |
| [Dashboard UI 设计](dashboard-ui-design.md) | 模板、实例、能力状态、节点工作台和主题 | 当前 UI 基线 |
| [Dashboard 实现计划](dashboard-implementation-plan.md) | UI 迭代与实现拆分 | 实施参考 |

## 3. 使用与配置

- [Getting Started](getting-started.md)：本地启动和首次使用
- [Quickstart](quickstart.md)：快速运行示例
- [Usage Guide](usage-guide.md)：常用操作说明
- [Configuration](configuration.md)：配置项和运行模式
- [FAQ](faq.md)：常见问题
- [Agent Guide](agent-guide.md)：历史 Agent 接入说明（当前执行以 ACP 执行设计为准）

## 4. 执行引擎与状态

- [ACP Execution](acp-execution.md)：ACP 执行链路
- [User Journey Map](user-journey-map.md)：用户旅程、页面导航和体验优化
- [Task Model Execution](task-model-execution.md)：任务模型执行语义
- [Task Model Execution TODO](task-model-execution-todo.md)：执行能力待办
- [Status State Machine](status-state-machine.md)：状态机和转换
- [Adaptive Control](adaptive-control.md)：自适应控制
- [Evaluator Loop](evaluator-loop.md)：评估与反馈循环
- [Agent Retry Pattern](agent-retry-pattern.md)：Agent 重试模式
- [Internal Review Loop](internal-review-loop.md)：内部审查循环
- [Human Task Interaction](human-task-interaction.md)：人工消息和任务交互
- [Message Bus](message-bus.md)：消息总线
- [Dynamic Tasks](dynamic-tasks.md)：动态任务

## 5. 变更、失败与副作用

- [Change Requirement](change-requirement.md)：变更需求
- [ChangeSets](changesets.md)：变更集和审批
- [Incremental Invalidation](incremental-invalidation.md)：增量失效与重跑
- [Failure Envelope](failure-envelope.md)：失败错误包络
- [Side Effects](side-effects.md)：外部副作用、幂等和补偿
- [Resource Versioning](resource-versioning.md)：资源版本化
- [Proposal Protocol](proposal-protocol.md)：提案协议
- [Memory Model](memory-model.md)：记忆和上下文模型

## 6. 部署、存储与工程治理

- [Storage Modes](storage-modes.md)：历史存储模式说明（当前架构以新五平面设计为准）
- [Production Hardening Status](production-hardening-status.md)：生产加固状态
- [Production Hardening TODO](production-hardening-todo.md)：生产加固待办
- [Architecture Decision 001](adr/001-dashboard-boundaries.md)：Dashboard 边界决策

## 7. 历史文档说明

以下文档仍保留用于查阅旧实现、测试夹具或迁移背景，但不作为当前产品和执行架构的实施依据：

- `agent-guide.md`、`storage-modes.md`、`configuration.md`、`concepts.md`；
- `task-model-execution.md`、`dashboard-implementation-plan.md`；
- `quickstart.md`、`getting-started.md` 中使用旧 `/api/workflows` 或旧 Worker 的示例。

当前实施统一参照 [产品需求](product-requirements.md)、[用户旅程](user-journey-map.md)、[系统架构](architecture.md)、[实现设计](../DESIGN.md) 和 [ACP 执行](acp-execution.md)。

## 8. 主题检索

| 你要查什么 | 先查 |
|---|---|
| 模板和实例的区别 | [产品需求](product-requirements.md)、[领域模型](../CONTEXT.md) |
| 一个模板创建多个实例 | [详细需求](job-flow-template-instance-requirements.md) |
| V1/V2 是否互相影响 | [产品需求](product-requirements.md) 第 3、4、5 节 |
| JSON 如何变成 DAG | [产品需求](product-requirements.md) 第 3.2 节、[详细需求](job-flow-template-instance-requirements.md) 第 10 节 |
| 模板详情页是否展示 DAG | [产品需求](product-requirements.md) 第 5.2、7 节 |
| 发布、草稿、发布并创建实例 | [产品需求](product-requirements.md) 第 4.3、5.1 节 |
| 实例删除条件 | [产品需求](product-requirements.md) 第 4.2 节、[详细需求](job-flow-template-instance-requirements.md) 第 6 节 |
| 从运行实例修改并重执行 | [产品需求](product-requirements.md) 第 5.5 节、[详细需求](job-flow-template-instance-requirements.md) 第 7 节 |
| Project Group 关系 | [领域模型](../CONTEXT.md)、[详细需求](job-flow-template-instance-requirements.md) 第 3.4、10.0 节 |
| API 和前端路由 | [实现设计](../DESIGN.md) 第 8、11 节 |
| Agent、Skill、MCP 如何装配 | [系统架构](architecture.md) 第 3、4、5 节、[ACP 执行](acp-execution.md) |
| 能力缺失为什么不启动 | [产品需求](product-requirements.md) 第 5.6 节、[ACP 执行](acp-execution.md) 第 3 节 |
| Execution Manifest 是什么 | [领域模型](../CONTEXT.md)、[实现设计](../DESIGN.md) 第 3.4 节 |
| 模板页面和实例页面怎么区分 | [用户旅程图](user-journey-map.md)、[Dashboard UI 设计](dashboard-ui-design.md) |
| 状态机和运行控制 | [状态机](status-state-machine.md)、[实现设计](../DESIGN.md) |
| 测试和验收 | [产品需求](product-requirements.md) 第 11 节、[详细需求](job-flow-template-instance-requirements.md) 第 12 节 |

## 9. 文档维护规则

- 新增产品规则时，先更新产品需求或详细需求，再同步实现设计；
- 新增或变更领域术语时，先更新 CONTEXT.md；
- 实现完成后，在实现设计中补充实际 API、路由、数据和测试状态；
- 文档出现冲突时，按本文第 1 节的优先级处理；
- 每份关键文档应在文件开头标注版本、状态和更新时间；
- 需求尚未确认时标记为“待确认”，不要把方案假设写成已确认规则。
- stage-bridge 已从目标执行架构移除；旧文档若仍描述它，只能视为待清理的历史材料，不得作为实施依据。
