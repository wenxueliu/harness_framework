# 证据驱动的自适应控制

自适应控制让长时间运行的 DAG 任务可以被中断、检查和恢复。DAG 仍然只负责
调度与影响范围，不会因为“回到上游”而增加环；恢复操作会创建 successor run，
归档旧结果，并重新调度从恢复目标开始的下游闭包。

## 核心模型

任务执行状态和结论有效性相互独立：

| 维度 | 值 | 含义 |
|------|----|------|
| `status` | `BLOCKED`、`PENDING`、`IN_PROGRESS`、`WAITING_FOR_HUMAN`、`DONE`、`FAILED` 等 | 当前是否可执行、正在执行或已结束 |
| `validity` | `UNKNOWN`、`VALID`、`STALE`、`INVALIDATED` | 任务产出的结论是否仍适用于当前资源版本 |

新任务以 `UNKNOWN` 开始。正常完成或检查通过后变为 `VALID`；需求变更和恢复
路由会把受影响的 DAG 下游闭包设为 `INVALIDATED`。`STALE` 可用于表达“已有
更新，尚待重新评估”，不会自动等同于执行失败。

## 原子动作协议

一个 Agent 每次只执行一个有围栏保护的业务动作：

1. 调用 `GET .../adaptive/next` 获取或恢复当前动作。
2. 执行动作，并保留命令、产物和 workspace revision 等证据。
3. 调用 `POST .../adaptive/check` 提交证据并消费动作。
4. 再次读取 `next`；此时 Harness 返回待处理路由边界。
5. 调用 `POST .../adaptive/route` 完成任务，或回到最早失效任务。

动作回执绑定 `action_id`、`attempt_id` 和 `state_version`。重复读取同一动作是
幂等的；过期 attempt、错误 action ID、旧 state version 或并发消费会被拒绝。

### 获取动作

```text
GET /api/workflow/REQ/task/TASK/adaptive/next
    ?actor=AGENT&attempt_id=ATTEMPT&type=EXECUTE
```

`type` 可为 `EXECUTE` 或 `VERIFY`。如果存在更高优先级边界，响应会改为
`ABORT`、`PAUSE`、`AWAIT_HUMAN`、`INTERPRET_FEEDBACK` 或 `ROUTE`，不会签发
新的业务动作。

### 提交检查证据

```json
POST /api/workflow/REQ/task/TASK/adaptive/check
{
  "action_id": "act-...",
  "state_version": 4,
  "verdict": "PASS",
  "verifier": "pytest",
  "actor": "agent-1",
  "workspace_revision": "commit-sha",
  "evidence": {"tests": 12, "failed": 0},
  "command": {
    "argv": ["python", "-m", "pytest", "tests/test_api.py"],
    "cwd": ".",
    "exit_code": 0,
    "output_digest": "sha256:..."
  },
  "artifact_refs": ["artifacts/test-report"]
}
```

`verdict` 只能是 `PASS`、`FAIL` 或 `ERROR`。`command.argv` 必须是非空字符串
数组，`exit_code` 必须是整数；不要只提交“测试通过”这样的无来源文本。

### 检查后的路由

失败时把任务路由到已经访问过的当前任务或祖先任务：

```json
POST /api/workflow/REQ/task/test/adaptive/route
{
  "target_task": "implementation",
  "reason": "失败来自实现而不是需求",
  "evidence": "TC-12 expected PAID, observed PENDING",
  "still_valid": ["requirements"],
  "invalidated": ["implementation", "test"],
  "failure_fingerprint": "order-state-TC-12",
  "actor": "agent-1"
}
```

Harness 会验证：

- `target_task` 是当前任务或其祖先，且已经存在执行状态；
- `invalidated` 恰好等于 `target_task` 的完整 DAG 下游闭包；
- `still_valid` 与失效闭包不重叠；
- 没有把缺少补偿任务的外部副作用节点选为恢复目标；
- 总路由次数、同一边次数和同一失败指纹次数均未超过预算。

检查通过后，允许目标中会增加 `__complete__`。完成路由要求当前任务仍为
`IN_PROGRESS`、`invalidated` 为空、当前任务包含在 `still_valid`，并且
CompletionContract 的所有 artifact 和 gate 均已满足。

## 可运行示例：失败后回到实现任务

先在一个终端启动带 HTTP 接口的本地模式：

```bash
python -m harness_framework.daemon --local
```

在另一个终端初始化示例，并模拟上游任务已完成、测试任务正在执行：

```bash
python scripts/sync_to_consul.py examples/adaptive-control.json --publish

KV=http://127.0.0.1:8500/v1/kv/workflows/adaptive-demo/tasks
curl -s -X PUT "$KV/requirements/status" -d DONE
curl -s -X PUT "$KV/requirements/validity" -d VALID
curl -s -X PUT "$KV/implementation/status" -d DONE
curl -s -X PUT "$KV/implementation/validity" -d VALID
curl -s -X PUT "$KV/test/status" -d IN_PROGRESS
```

获取一个验证动作：

```bash
ACTION=$(curl -s \
  "http://127.0.0.1:8080/api/workflow/adaptive-demo/task/test/adaptive/next?actor=test-agent&attempt_id=attempt-1&type=VERIFY")
echo "$ACTION" | python3 -m json.tool
```

下面的命令从回执读取围栏字段，并提交一次可复现的失败证据：

```bash
CHECK_BODY=$(ACTION_JSON="$ACTION" python3 -c '
import json, os
a = json.loads(os.environ["ACTION_JSON"])
print(json.dumps({
    "action_id": a["action_id"],
    "state_version": a["state_version"],
    "verdict": "FAIL",
    "verifier": "contract-test",
    "actor": "test-agent",
    "evidence": {"case": "users-12", "expected": 200, "actual": 500},
    "command": {
        "argv": ["python", "-m", "pytest", "tests/test_users.py"],
        "cwd": ".", "exit_code": 1, "output_digest": "sha256:demo"
    }
}))')

curl -s -X POST \
  http://127.0.0.1:8080/api/workflow/adaptive-demo/task/test/adaptive/check \
  -H 'Content-Type: application/json' -d "$CHECK_BODY" | python3 -m json.tool

curl -s \
  "http://127.0.0.1:8080/api/workflow/adaptive-demo/task/test/adaptive/next?actor=test-agent" \
  | python3 -m json.tool
```

最后将失败路由到 `implementation`。线性 DAG 中它的下游闭包正好是
`implementation` 和 `test`：

```bash
curl -s -X POST \
  http://127.0.0.1:8080/api/workflow/adaptive-demo/task/test/adaptive/route \
  -H 'Content-Type: application/json' \
  -d '{
    "target_task": "implementation",
    "reason": "契约检查证明实现返回错误状态",
    "evidence": "users-12 expected 200, observed 500",
    "still_valid": ["requirements"],
    "invalidated": ["implementation", "test"],
    "failure_fingerprint": "users-contract-12",
    "actor": "test-agent"
  }' | python3 -m json.tool
```

响应包含 `previous_run_id`、`new_run_id` 和已消费的预算状态。此时
`implementation` 为 `PENDING`，`test` 为 `BLOCKED`，两者的 `validity` 均为
`INVALIDATED`；Harness 没有修改 DAG 或创建回边。

## 路由预算

预算存放在 `workflows/<req>/routing/budget`：

```json
{
  "policy": {
    "max_total_routes": 8,
    "max_same_edge_routes": 2,
    "max_same_failure_fingerprint": 2
  },
  "state": {"total": 0, "edges": {}, "fingerprints": {}}
}
```

任一上限耗尽后，源任务转为 `WAITING_FOR_HUMAN`，并打开结构化人工问题。
配置命令见[配置参考](configuration.md#自适应路由预算)。

## 人工反馈

人工消息使用独立生命周期：

```text
DELIVERED → OBSERVED → ACKNOWLEDGED → APPLIED
```

| 操作 | 端点 | 说明 |
|------|------|------|
| 发送消息 | `POST .../adaptive/feedback` | 写入 `message`、`actor`，可附 `kind` 和 `source` |
| 查看消息 | `GET .../adaptive/feedback` | 返回该任务全部人工反馈 |
| 观察边界 | `GET .../adaptive/next?actor=...` | 把最早未处理消息从 `DELIVERED` 变为 `OBSERVED` |
| 响应消息 | `POST .../adaptive/respond` | 选择 `CONTINUE`、`ASK` 或 `PAUSE` |
| 回答问题 | `POST .../adaptive/answer` | 恢复问题打开前的任务状态，并生成 answer feedback |

`CONTINUE` 表示反馈已应用，后续仍要重新验证；`ASK` 必须附带包含 `text` 和
`options` 的 `question`，并把任务置为 `WAITING_FOR_HUMAN`；`PAUSE` 设置任务级
暂停信号。

## 安全边界与宿主 Hook

边界优先级固定为：

```text
ABORT > PAUSE > AWAIT_HUMAN > FEEDBACK > ROUTE > ACTIVE
```

`skills/stage-bridge/scripts/adaptive_boundary.py` 可由 Claude Code、Codex 或其他
宿主在 user-message、pre-tool 和 post-tool hook 中调用：

```bash
export REQ_ID=adaptive-demo
export TASK_NAME=test
export AGENT_ID=test-agent
export HARNESS_API=http://127.0.0.1:8080

python skills/stage-bridge/scripts/adaptive_boundary.py pre-tool
```

退出码 `0` 表示可继续，`6` 表示业务工具被非 ABORT 边界阻断，`7` 表示
ABORT。暂停、恢复和中止等控制操作应附 `--control-operation`，以免控制本身被
已有边界拦住。

## 经评估的需求变更

当需求变化时，调用方必须同时提交证据和影响集合：

```json
POST /api/workflow/REQ/requirement-change/assessed
{
  "content": "新的需求文本",
  "reason": "接口契约改变",
  "still_valid": ["design"],
  "invalidated": ["backend", "test"],
  "evidence": "API 响应契约已改变",
  "actor": "alice"
}
```

Harness 从 `invalidated` 推导最小 changed roots，验证其下游闭包，发布新需求
revision，滚动到 successor run，并记录 `GOAL_REVISED` 审计事件。

## 审计事件

事件写在 `workflows/<req>/events/` 下，包含 actor、时间、run/task、causation ID、
correlation ID 和 payload。动作签发、检查、路由、失效、反馈、问题、控制和需求
修订都会留下事件。

## 常见拒绝

| 错误码 | 原因 |
|--------|------|
| `E_BOUNDARY_BLOCKED` | PAUSE、ABORT、人工问题、反馈或待路由尚未处理 |
| `E_STALE_ATTEMPT` | attempt 已不再拥有任务 |
| `E_STALE_ACTION` | action ID 或 state version 不是当前值 |
| `E_INVALID_CLOSURE` | `invalidated` 不是恢复目标的完整下游闭包 |
| `E_COMPLETION_CONTRACT` | artifact、gate 或 circuit breaker 尚未满足完成条件 |
| `E_ROUTING_BUDGET_EXHAUSTED` | 路由预算耗尽，任务已转人工处理 |

自适应 API 的业务冲突返回 HTTP `409`，响应同时包含稳定的 `error` 代码、可读
`message` 和可选 `details`，Agent 应根据错误码处理，不能靠匹配消息文本恢复。
