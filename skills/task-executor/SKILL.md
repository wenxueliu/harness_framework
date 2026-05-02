---
name: task-executor
description: |
  任务执行器。Worker 抢到任务后加载本 Skill，按照任务类型（backend/test/design/review）
  在独立 worktree 中依次执行 TDD subagent、API test subagent、reviewer subagent，
  经过多轮反馈迭代后完成或失败。适配自 multiagents 的 hw-worktree-controller + hw-tdd-agent 工作流。
  Triggers: "执行任务", "task executor", "TDD workflow", "backend task",
  "test task", "worktree", "实现功能", "开发任务"
---

# Task Executor

Worker 抢到任务后，加载本 Skill 执行实际开发工作。

## 执行模型

```
Worker 抢到任务 (IN_PROGRESS)
  │
  ├── 1. 加载任务上下文（Consul KV）
  ├── 2. 创建/进入 git worktree
  ├── 3. 按任务类型执行工作流:
  │     ├── backend:  TDD → API test → reviews → merge
  │     ├── test:     E2E → report → feedback loop
  │     ├── design:   Architecture doc → review → approve
  │     ├── review:   Code/doc review → findings
  │     └── deploy:   Deploy → verify
  ├── 4. 处理反馈迭代（最多 3 轮）
  └── 5. 标记任务 DONE 或 FAILED
```

## 任务类型工作流

| 任务类型 | 工作流 | 参考 |
|---------|--------|------|
| `backend` | UT RED→GREEN→REFACTOR → API RED→GREEN→REFACTOR → 并行 Review → 修复 → Merge | `references/backend-task-workflow.md` |
| `test` | 运行 E2E → 发现失败 → Message Bus 通知 → 等待修复 → 重测 (≤3轮) | `references/test-task-workflow.md` |
| `design` | 编写设计文档 → 审查 → 修订 → 写入产物 | `references/design-task-workflow.md` |
| `review` | 加载被审产物 → 按审查维度检查 → 输出审查报告 | `references/review-task-workflow.md` |
| `deploy` | 构建 → 部署 → 冒烟测试 → 写入产物 | `references/deploy-task-workflow.md` |

## On Activation

### 1. 读取任务上下文

从 Consul KV 加载任务完整信息:

```bash
# 任务元数据
python3 skills/stage-bridge/scripts/claim_task.py <req_id> <task_name>
# 或: 任务已在 worker 抢占时加载，从 stdin/环境变量获取
```

关键上下文信息:
- `task_meta.type` — 任务类型，决定执行哪个工作流
- `task_meta.service_name` — 服务名，用于 worktree 和 repo 操作
- `task_meta.description` — 任务描述
- `task_meta.metadata.test_bindings` — 绑定的测试用例
- `task_meta.metadata.review_requirements` — 需要的审查类型
- `context.*` — 上游任务写入的需求级上下文（API spec, design doc 等）

### 2. 创建 Worktree

```bash
TASK_NAME="<task_name>"
WORKTREE_BASE="${WORKTREE_BASE:-.worktree}"
WORKTREE_PATH="$WORKTREE_BASE/$TASK_NAME"
BRANCH="hw-$TASK_NAME"

# 创建 worktree
git worktree add "$WORKTREE_PATH" -b "$BRANCH"

# 进入 worktree
cd "$WORKTREE_PATH"
```

### 3. 执行任务

根据 `task_meta.type` 选择对应的参考文件执行工作流。

### 4. 报告结果

通过 stage-bridge 脚本写入:
- 步骤日志: `python3 skills/stage-bridge/scripts/log_step.py`
- 产物: `python3 skills/stage-bridge/scripts/write_artifact.py`
- 完成: `python3 skills/stage-bridge/scripts/complete_task.py`
- 失败: `python3 skills/stage-bridge/scripts/fail_task.py`

## TDD 铁律

**NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST.** 两层都遵循:
- Layer 1 (UT): 先写失败的单元测试 → 实现 → 重构
- Layer 2 (API): 先写失败的 API 测试 → 实现端点 → 重构

每层通过后才能进入下一层。违反铁律 → 删除代码重来。

## 反馈闭环

```
┌─────────┐    FIX 消息     ┌─────────┐
│ Test    │ ───────────────▶ │ Backend │
│ Agent   │                  │ Agent   │
│         │ ◀─────────────── │         │
└─────────┘   修复完成       └─────────┘
     │                            │
     │ 重测                       │
     └────────────────────────────┘
             最多 3 轮
```

Test Agent 发现失败 → 通过 Message Bus 发送 FIX 消息 → Backend Agent 接收并修复 → Test Agent 重测。
3 轮仍未通过 → FAILED。

## ABORT 检测

每个关键步骤前检查:
```bash
python3 skills/stage-bridge/scripts/check_control.py <req_id>
# exit code 7 = ABORT → 立即退出
```

检查节点:
- 每次 LLM 调用前后
- 每个 TDD 阶段开始前
- 每个 review 开始前
- merge 前

## 迭代管理

| 阶段 | 最大迭代 | 超出后行为 |
|------|---------|-----------|
| UT RED → GREEN | 3 次/stub | 标记 FAILED |
| API RED → GREEN | 3 次/端点 | 标记 FAILED |
| Review → Fix | 3 轮 | 记录 P2+ 问题，标记 DONE_WITH_CONCERNS |
| Test → Fix → Retest | 3 轮 | 标记 FAILED |

## 原则

- **TDD iron law** — 先测试，后代码。不可违反。
- **自包含验证** — 每个任务在其 worktree 内独立验证（UT + API test）。
- **纵向切片** — 不做横切任务。UT 和 API test 随代码一起完成。
- **最小实现** — 只写让测试通过的最少代码。
- **透明上报** — 每步记录日志到 Consul 会话流。
- **ABORT 必检** — 全链路关键节点检查 ABORT 信号。
