# Production Hardening Status

最后核对：2026-08-02。权威逐项清单仍是
[production-hardening-todo.md](production-hardening-todo.md)；本页用于说明当前可用能力和剩余发布工作。

## 已完成

- P0 scheduling：显式 fork/join、all/any/quorum、失败传播、无可运行工作时终止。
- P0 dynamic DAG：冲突检测、缺失依赖/环/碰撞校验、Proposal 冻结与拒绝历史。
- P0 ownership：不可变 attempt、单调 lease、软/硬超时与 stale-worker fencing。
- P1 contracts：AgentContract、Artifact Manifest、verifier evidence、completion gates、evaluator loop。
- P1 incremental delivery：四类资源独立版本、ChangeSet、影响闭包、精准失效和 run roll-forward。
- P1 context/long-running：六类 knowledge namespace、显式 context inputs、有界摘要、checkpoint、资源预算熔断。
- P1 failure/recovery：七类 Failure Envelope、副作用幂等与补偿、四级恢复路径。

对应实现分为四个可回滚提交：

| Commit | 范围 |
|--------|------|
| `e900866` | evaluator loop 与增量交付 |
| `d1a326a` | context 隔离与长任务约束 |
| `2ac1ff3` | Failure Envelope |
| `2746f0f` / `58e1866` | 副作用补偿与恢复路径 |

## 尚未完成

P2 仍需完成：

1. Requirement → Run → Attempt → Agent Call → Tool Call trace/span。
2. Model、latency、tokens、cost、confidence 与 structured outcome telemetry。
3. per-role least-privilege tool/data enforcement。
4. 由 Harness runtime state 生成 `multiagents` requirement tracker。
5. 迁移与向后兼容指南。

随后还需通过属性测试、故障注入、需求执行中变更和 checkpoint recovery
E2E，并统一全部状态/schema 词汇。

## 当前测试基线

最近一次非网络单元门禁：`294 passed, 13 deselected`。被排除的 13 项均属于
`TestLocalConsulHTTP`，当前执行沙箱禁止创建本地监听 socket。它们尚未通过，
因此 Release Gate “unit tests pass without collection errors” 保持未勾选，不能将
该基线解释为完整发布通过。
