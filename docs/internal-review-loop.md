# 单任务 Executor–Reviewer 闭环

适用于代码审查、测试检查和“生成—验证—修订”紧密耦合的任务。框架只调度一个任务，Worker 在同一个 attempt 内依次调用 Executor 与独立 Reviewer：

```text
IN_PROGRESS
  └─ Executor → Reviewer
         ▲          │
         └─ CHANGES_REQUIRED（最多 max_rounds）
                    │ PASS
                    ├─ DONE
                    └─ AWAITING_REVIEW（需要人工确认）
```

安全、合规或独立排期的正式门禁仍应建成单独 DAG 任务。

## Workflow 配置

```json
{
  "implement-login": {
    "type": "backend",
    "service_name": "user-service",
    "depends_on": [],
    "description": "实现用户登录功能",
    "context_inputs": [
      "summaries/login-requirement",
      "artifacts/login-api-design"
    ],
    "review_policy": {
      "max_rounds": 3,
      "dimensions": [
        "requirement-conformance",
        "correctness",
        "error-handling",
        "test-coverage"
      ],
      "blocking_severities": ["CRITICAL", "HIGH"],
      "require_independent_agent": true,
      "human_approval_after_pass": true,
      "allowed_recovery_targets": ["implement-login", "design-login"],
      "default_recovery_target": "implement-login"
    },
    "completion_contract": {
      "required_artifacts": ["implementation", "review-report"],
      "required_gates": ["unit-tests", "review"]
    }
  }
}
```

声明 `review_policy` 的任务必须把 `review` 放入 `completion_contract.required_gates`。同步脚本会拒绝缺少该门禁的 Workflow。

## 启动 Worker

Executor 和 Reviewer 都采用 JSON stdin/stdout 协议：

```bash
python skills/stage-bridge/scripts/worker.py \
  --service user-service \
  --capabilities dev \
  --repo-path /code/user-service \
  --executor "python /opt/agents/login_executor.py" \
  --reviewer "python /opt/agents/code_reviewer.py" \
  --review-timeout 1800
```

配置了 `review_policy` 但 Worker 没有 `--reviewer` 时，任务会失败，不会绕过 Review。

## Executor 输入与输出

第一轮输入：

```json
{
  "req_id": "req-001",
  "task_name": "implement-login",
  "round": 1,
  "attempt_id": "attempt-...",
  "lease_epoch": 1,
  "task_meta": {},
  "context": {},
  "review_feedback": null,
  "config": {
    "agent_id": "executor-1",
    "service_name": "user-service",
    "repo_path": "/code/user-service",
    "worktree_base": ".worktree"
  }
}
```

Reviewer 要求修改后，下一轮的 `review_feedback` 是上一轮完整 ReviewResult。Executor 成功时输出：

```json
{
  "status": "DONE",
  "artifact_refs": ["commit:abc123"],
  "test_results": {"unit-tests": "42 passed"}
}
```

业务执行失败时输出 `{"status":"FAILED","error":"..."}`，Worker 不再调用 Reviewer。

## Reviewer 输入与输出

Reviewer 收到结构化 Review Package，不接收 Executor 的私有思考历史：

```json
{
  "req_id": "req-001",
  "task_name": "implement-login",
  "round": 1,
  "attempt_id": "attempt-...",
  "task": {
    "description": "实现用户登录功能",
    "agent_contract": {}
  },
  "acceptance": {
    "completion_contract": {},
    "dimensions": ["correctness"],
    "blocking_severities": ["CRITICAL", "HIGH"]
  },
  "context": {},
  "execution_result": {}
}
```

Reviewer 必须输出：

```json
{
  "verdict": "CHANGES_REQUIRED",
  "reviewer": "review-agent-2",
  "summary": "失败计数更新存在竞态",
  "findings": [
    {
      "id": "REV-001",
      "severity": "HIGH",
      "location": "src/auth/login.py:82",
      "message": "读取和更新不是原子操作",
      "blocking": true
    }
  ],
  "criteria": [],
  "artifact_refs": ["commit:abc123"]
}
```

`verdict` 仅允许：

- `PASS`：Review 通过；Worker 自动写入 `evidence/review=PASS`。
- `CHANGES_REQUIRED`：把结果原样反馈给下一轮 Executor。
- `ERROR`：Reviewer 自身无法完成，任务失败。

当 `require_independent_agent=true` 时，`reviewer` 必填，且不能等于执行 Worker 的 `agent_id`。

`CHANGES_REQUIRED` 可以通过 `recovery_target` 指定整改任务：

```json
{
  "verdict": "CHANGES_REQUIRED",
  "reviewer": "review-agent-2",
  "summary": "接口设计缺少锁定状态",
  "recovery_target": "design-login",
  "findings": []
}
```

目标是当前任务时，Worker 在同一 attempt 内继续下一轮。目标是合法上游任务时，框架归档该目标及其下游的 artifact/evidence，清除旧 attempt，将目标置为 `PENDING`、下游置为 `BLOCKED`，并把反馈写入目标任务的 `recovery_feedback/current`。目标必须位于 `allowed_recovery_targets`，且必须是当前任务或其 DAG 祖先；Reviewer 不能跳转到无关任务。

每轮输入输出持久化到：

```text
tasks/<task>/review/attempts/<attempt_id>/rounds/<round>/
```

## 人工确认

`human_approval_after_pass=true` 时，自动 Review PASS 后任务进入 `AWAITING_REVIEW`。

批准：

```bash
curl -X POST http://127.0.0.1:8080/api/workflow/req-001/task/implement-login/approve \
  -H 'Content-Type: application/json' \
  -d '{"actor":"alice","comment":"验收通过"}'
```

拒绝：

```bash
curl -X POST http://127.0.0.1:8080/api/workflow/req-001/task/implement-login/reject \
  -H 'Content-Type: application/json' \
  -d '{"actor":"alice","comment":"接口定义需要补充锁定状态","recovery_target":"design-login"}'
```

批准使任务进入 `DONE`。拒绝使用相同的受限恢复目标机制；省略 `recovery_target` 时使用 `default_recovery_target`。人工意见写入目标任务的 `recovery_feedback`，并在下一次领取后注入 Executor。生产部署时应由反向代理或认证层提供并审计真实用户身份；当前 API 要求显式提交 `actor`，本身不提供身份认证。

## 失败语义

- Agent 失联、lease 过期：Watchdog 进行基础设施重试。
- `CHANGES_REQUIRED`：同一 attempt 内继续下一轮，不增加 Watchdog `retry_count`。
- 超过 `max_rounds`：任务 `FAILED`。
- Reviewer 返回 `ERROR` 或协议无效：任务 `FAILED`。
- 人工拒绝：任务回到 `PENDING`，产生新的 attempt。
- 上游问题：回退到允许的祖先任务，并失效其下游闭包。
