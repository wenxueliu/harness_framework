# 状态机

> **初次接触？** 先看 [concepts.md](concepts.md) 了解核心状态流转。本文是全部状态和转移条件的完整定义。

## 需求状态

需求（Workflow）级别状态，控制整体调度行为。

```
DRAFT ──→ CONFIRMED ──→ IN_PROGRESS ──→ DONE
  │           ↑
        Proposal（中间态）
```

| 状态 | 含义 | Aggregator 行为 |
|------|------|----------------|
| `DRAFT` | 草稿，尚未启动 | 不调度 |
| `CONFIRMED` | 已确认，等待或执行中 | 正常调度 |
| `Proposal` | 有 Agent 发现遗漏任务，暂停调度 | **暂停调度**，等待人工确认 |
| `IN_PROGRESS` | 有任务正在执行 | 正常调度 |
| `DONE` | 全部任务完成 | 结束 |

### 状态转换规则

- `DRAFT` → `CONFIRMED`：人工发布需求（设置 `published=true`）
- `CONFIRMED` → `Proposal`：Agent 提出新任务（见 [proposal-protocol.md](./proposal-protocol.md)）
- `Proposal` → `CONFIRMED`：人工确认或拒绝提案
- `CONFIRMED` → `IN_PROGRESS`：第一个任务被 Agent 抢占
- `IN_PROGRESS` → `DONE`：所有任务 DONE

## 任务状态

任务（Task）级别状态，由 Agent 和 Aggregator 共同维护。

```
          ┌──────────────────────────────────────────┐
          ↓                                          │
BLOCKED ──→ PENDING ──→ IN_PROGRESS ──→ DONE        │
                                              ↓       │
                                        FAILED ──────┘
                                              ↓
                                       AWAITING_REVIEW
                                              ↓
                                            DONE
```

| 状态 | 含义 | 写入方 |
|------|------|--------|
| `BLOCKED` | 有依赖未完成 | Aggregator（初始化时） |
| `PENDING` | 依赖满足，等待 Agent 抢占 | Aggregator |
| `IN_PROGRESS` | Agent 正在执行 | Agent（claim 后） |
| `DONE` | 任务完成 | Agent |
| `FAILED` | 任务失败 | Agent 或 Watchdog |
| `AWAITING_REVIEW` | 等待人工 Code Review | Agent（`complete_task.py --await-review`） |
| `SKIPPED_UPSTREAM_FAILED` | 阻塞依赖已失败，任务不会执行 | Aggregator |

### 状态转换规则

**初始状态**：
- 无 `depends_on` 的任务 → `PENDING`
- 有 `depends_on` 的任务 → `BLOCKED`

**Aggregator 激活**：
- `BLOCKED` → `PENDING`：所有上游依赖全部 DONE

**Agent 操作**：
- `PENDING` → `IN_PROGRESS`：Agent CAS 抢占任务
- `IN_PROGRESS` → `DONE`：Agent 完成任务
- `IN_PROGRESS` → `FAILED`：Agent 报告失败
- `DONE` → `AWAITING_REVIEW`：Agent 请求人工 Review

**Watchdog 恢复**：
- `IN_PROGRESS` → `PENDING`：Agent 死亡、软 lease 超时或硬超时（≤5 次重试）
- `IN_PROGRESS` → `FAILED`：重试次数超过上限

## Aggregator 调度逻辑

```
_tick():
  for each req_id (by priority desc):
    if not published: skip
    if control == PAUSE: skip
    if control == ABORT: abort_all_tasks()
    if status == Proposal: skip  # 暂停调度

    for each task in dependencies:
      if status not in (BLOCKED, ""): skip

      if all depends_on == DONE:
        activate task → PENDING
      else:
        set status = BLOCKED (if empty)
```

### parallel / aggregate 复合节点

| 节点类型 | 行为 |
|---------|------|
| `parallel` | 依赖满足后激活 `children` 并进入 IN_PROGRESS；仅当 join policy 满足后 DONE |
| `aggregate` | 上游 parallel 真正 DONE 后自身 DONE 并激活下游 |

`parallel.join.strategy` 支持 `all`（默认）、`any`、`quorum`。`quorum`
必须同时声明 `minimum_success`。当剩余 children 已不可能满足策略时，parallel
进入 `FAILED`，其阻塞下游进入 `SKIPPED_UPSTREAM_FAILED`。

## Watchdog 恢复逻辑

```
_tick():
  for each req_id (if published):
    for each task (status == IN_PROGRESS):
      if not agent_alive(task.assigned_agent):
        rollback_task(task)  # retry_count++, PENDING or FAILED
      elif task.started_at + hard_timeout < now:
        rollback_task(task, reason="hard_timeout")
      elif task.lease_expires_at < now:
        rollback_task(task, reason="soft_timeout")
```

Agent claim 时获得不可变的 `attempt_id` 与单调递增的 `lease_epoch`，并写入
`lease_expires_at` 和固定的 `hard_deadline_at`。当前 attempt 可通过
`renew_lease.py` 周期续租；续租只延长软 lease，且不会越过硬截止。因此失联
worker 会在软超时后快速回收，持续活跃但运行过久的 worker 仍会在硬超时后
被 fence。

重试策略：
- `retry_count` < 5 → 回滚为 `PENDING`，允许重新抢占
- `retry_count` >= 5 → 标记为 `FAILED`

## Consul KV 结构速查

```
workflows/<req_id>/
├── status              # 需求状态
├── published           # true | false
├── control             # PAUSE | RESUME | ABORT
├── dependencies        # 任务 DAG
└── tasks/<task_name>/
    ├── status          # 任务状态
    ├── type            # design | review | backend | test | deploy | parallel | aggregate
    ├── assigned_agent  # 抢占的 Agent
    ├── started_at     # 开始时间
    ├── activated_at    # 激活时间
    ├── retry_count     # 重试次数
    └── error_message   # 失败原因
```

## 相关文档

- [proposal-protocol.md](./proposal-protocol.md) — 动态任务提案协议
- [dynamic-tasks.md](./dynamic-tasks.md) — 动态任务创建机制
- [message-bus.md](./message-bus.md) — 任务间消息通信
