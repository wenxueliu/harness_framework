# Backend Task Workflow

后端开发任务的完整执行流程: 在独立 worktree 中完成 TDD → API Test → Review → Merge。

## 流程总览

```
Step 1: 工作区准备
Step 2: Layer 1 — UT RED→GREEN→REFACTOR
Step 3: Layer 2 — API Test RED→GREEN→REFACTOR
Step 4: 并行代码审查 (Security + Logic + Performance)
Step 5: 质量门禁检查
Step 6: 反馈迭代修复 (如有)
Step 7: Merge + 清理
```

## Step 1: 工作区准备

### 1.1 加载任务信息

从 Consul KV 获取:
- 任务元数据: `workflows/<req_id>/tasks/<task_name>/`
- 上游上下文: `workflows/<req_id>/context/`（API spec, design doc, ADR 等）
- 测试绑定: `task_meta.metadata.test_bindings`

### 1.2 确认需求

```bash
# 读取设计文档
python3 skills/stage-bridge/scripts/read_context.py <req_id> api_spec
python3 skills/stage-bridge/scripts/read_context.py <req_id> design_doc
```

### 1.3 创建 Worktree

```bash
cd $REPO_PATH
TASK_SLUG=$(echo "$TASK_NAME" | tr '/' '-')
BRANCH="hw-$TASK_SLUG"
WORKTREE_PATH="$WORKTREE_BASE/$TASK_SLUG"

git worktree add "$WORKTREE_PATH" -b "$BRANCH"
cd "$WORKTREE_PATH"
```

### 1.4 初始化日志

```bash
python3 skills/stage-bridge/scripts/log_step.py <req_id> "<task_name>" \
  --type "SETUP" --message "Worktree 已创建: $BRANCH"
```

## Step 2: Layer 1 — UT (单元测试) RED → GREEN → REFACTOR

### 2.1 UT RED Phase

**目标:** 为所有目标方法编写失败的单元测试。

输入:
- 设计文档中的 UT 用例规格（Section 10.3）
- `task_meta.metadata.test_bindings.ut_cases`

操作:
1. 读取绑定的 UT 用例列表
2. 按用例逐一编写测试代码
3. 运行测试 → 必须 RED（全部失败）
4. 如果测试直接 GREEN → 检查测试是否正确

```
记录: "[UT-RED] 编写 {N} 个 UT 用例, 全部 RED"
```

**ABORT 检查必做。**

### 2.2 UT GREEN Phase

**目标:** 编写最少的生产代码使 UT 全部通过。

原则:
- 最小实现 — 只写让测试通过的代码
- 不添加测试未覆盖的功能
- 不优化 — 那是 REFACTOR 阶段的事

```
记录: "[UT-GREEN] {N}/{N} UT 通过"
```

### 2.3 UT REFACTOR Phase

**目标:** 改进代码结构，不改变行为。

检查:
- 消除重复代码
- 改善命名
- 提取公共方法
- 确认所有测试仍然 GREEN

```
记录: "[UT-REFACTOR] 重构完成, {N} UT 全部保持 GREEN"
```

### 2.4 UT Layer 门禁

- [ ] 所有 UT 用例 GREEN
- [ ] 覆盖目标 ≥ 90%（核心逻辑）
- [ ] 无跳过的测试
- [ ] 代码未经测试覆盖的部分已记录理由

## Step 3: Layer 2 — API Test RED → GREEN → REFACTOR

### 3.1 API RED Phase

**目标:** 编写失败的 API 测试（Postman Collection）。

输入:
- 设计文档中的 API 用例规格（Section 10.4）
- `task_meta.metadata.test_bindings.api_cases`
- OpenAPI spec（从上游 context 读取）

操作:
1. 创建 Postman Collection JSON 文件
2. 写入 API 测试用例（正常 + 异常 + 边界）
3. 运行 Newman → 必须全部失败（API 还未实现）

```bash
# 运行 API 测试
newman run "$TEST_DIR/api-${TASK_SLUG}.json" \
  -e "$TEST_DIR/api-env.json" --reporters cli,junit
```

```
记录: "[API-RED] 编写 {N} 个 API 用例, 全部 RED"
```

### 3.2 API GREEN Phase

**目标:** 实现 API 端点使 API 测试全部通过。

操作:
1. 实现 API 端点（controller + service + repository 层）
2. 运行 UT → 确认 UT 仍然 GREEN
3. 运行 API 测试 → 目标全部 GREEN

```
记录: "[API-GREEN] {N}/{N} API 用例通过"
```

### 3.3 API REFACTOR Phase

**目标:** 改进 API 实现质量。

检查:
- 错误处理完整
- 输入校验健壮
- 日志记录合理
- 所有测试保持 GREEN

```
记录: "[API-REFACTOR] API 重构完成"
```

### 3.4 API Layer 门禁

- [ ] 所有 API 用例 GREEN
- [ ] 覆盖: 正常 ×1 + 异常 ×1 + 边界 ×1 + 认证/授权 ×1
- [ ] UT 层仍全部 GREEN
- [ ] Postman Collection JSON 已保存到测试目录

## Step 4: 并行代码审查

根据 `task_meta.metadata.review_requirements` 启动审查子 Agent:

### 4.1 启动审查

对每个 review_requirement，并行启动对应的 reviewer subagent:

```
review_requirements:
  - reviewer: security → 启动 hw-reviewer-security subagent
  - reviewer: logic    → 启动 hw-reviewer-logic subagent
  - reviewer: performance → 启动 hw-reviewer-performance subagent
```

### 4.2 审查产物

每个 reviewer 输出审查报告，包含:
- 问题列表（P0/P1/P2/P3 分级）
- 具体代码位置
- 修复建议

审查报告写入 Consul 会话流:
```bash
python3 skills/stage-bridge/scripts/log_step.py <req_id> "<task_name>" \
  --type "REVIEW" --message "<reviewer>: {N} issues (P0:{p0}, P1:{p1}, P2:{p2})"
```

## Step 5: 质量门禁检查

### 5.1 问题分级处理

| Level | 处理 | 门禁 |
|-------|------|------|
| P0 | 必须修复 | 阻塞所有阶段 |
| P1 | 必须修复 | 阻塞下一阶段 |
| P2 | 必须修复 | 阻塞下一阶段 |
| P3 | 记录 | 不阻塞 |

### 5.2 修复 + 重审

1. 修复 P0/P1/P2 问题
2. 重跑 UT + API 测试（确认修复未引入回归）
3. 修复后的代码提交 reviewer 重审
4. 最多 3 轮迭代

```
记录: "[GATE] P0:{fixed}, P1:{fixed}, P2:{fixed} — 门禁通过"
```

## Step 6: 反馈迭代

如果本任务是 test 任务的依赖（下游 test 发现本任务的 bug），处理 FIX 消息:

### 6.1 监听 FIX 消息

```bash
python3 skills/stage-bridge/scripts/message_poll.py <req_id>
```

### 6.2 修复流程

1. 接收 FIX 消息 → 理解错误
2. 编写复现测试 → RED
3. 修复代码 → GREEN
4. 确认所有已有测试 GREEN
5. 标记消息完成

```bash
python3 skills/stage-bridge/scripts/message_complete.py <req_id> <msg_id> \
  --result "FIXED" --summary "修复了 {描述}"
```

## Step 7: Merge + 清理

### 7.1 合并 Worktree

```bash
cd "$WORKTREE_PATH"
git add -A
git commit -m "feat($TASK_SLUG): <描述>

Implements: <req_id>/<task_name>
Generated with [Claude Code](https://claude.ai/code)"

# 合并回主分支
cd "$REPO_PATH"
git merge "$BRANCH"
```

### 7.2 写入产物

```bash
python3 skills/stage-bridge/scripts/write_artifact.py <req_id> "<task_name>" \
  branch "$BRANCH"
python3 skills/stage-bridge/scripts/write_artifact.py <req_id> "<task_name>" \
  commit "$(git rev-parse HEAD)"
```

### 7.3 清理 Worktree

```bash
git worktree remove "$WORKTREE_PATH"
git branch -d "$BRANCH"
```

### 7.4 记录完成

```bash
python3 skills/stage-bridge/scripts/log_step.py <req_id> "<task_name>" \
  --type "DONE" --message "任务完成: UT + API test 全部通过, 审查通过, 已合并"
python3 skills/stage-bridge/scripts/complete_task.py <req_id> <task_name>
```

## 门禁清单（提交前自检）

- [ ] UT 全部 GREEN，覆盖率 ≥ 目标
- [ ] API test 全部 GREEN，覆盖 4 类场景
- [ ] P0 问题 = 0
- [ ] P1 问题 = 0
- [ ] P2 问题 = 0（或已记录为 P3）
- [ ] 代码已 commit + merge
- [ ] Worktree 已清理
- [ ] 产物已写入 Consul
- [ ] 所有步骤日志已记录
