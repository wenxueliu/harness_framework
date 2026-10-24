# 人工介入任务

Dashboard 以 `workflow_id + task_name` 定位任务，再通过任务的 `attempt_id` 和
`acp/session_id` 关联某次执行与 provider 原生会话。前端不使用 `service_name`
或临时 `agent_id` 作为绑定键。

## 在 Dashboard 中操作

启动框架和 Dashboard 后，访问 <http://127.0.0.1:3000/>，点击任意任务卡片。
右侧任务抽屉包含：

- 最近 200 条标准化执行事件；
- 已发送的人工消息及处理状态；
- 人工消息输入框；
- `当前步骤后处理` 和 `中断并立即处理` 两种模式。

`queue` 不打断当前 ACP turn，Agent 当前步骤结束后在同一 session 中处理消息。
`interrupt` 会发送 ACP `session/cancel`，然后在同一 session 中立即开启一个包含
人工消息的新 turn。

如果目标任务已经 `DONE`、`FAILED`、`AWAITING_REVIEW` 或
`WAITING_FOR_HUMAN`，提交消息会创建恢复流程：目标任务及其 DAG 下游被标记为
失效，目标任务进入 `PENDING`，Dispatcher 创建新 attempt 并加载原 ACP
session。旧 attempt、session 和事件仍保留用于审计。

## HTTP API

查看整个实例的执行过程（每个节点 + 它每一轮 attempt）：

```bash
curl -s 'http://127.0.0.1:8080/api/instances/<instance_id>/attempts'
```

返回里每个节点带 `task_id`、`state`、`attempt_count`、`fail_count`、
`retry_feedback` 和 `attempts[]`。看板实例页的「执行过程」标签直接用这份数据。

回放某一轮的 Agent 会话：

```bash
curl -s 'http://127.0.0.1:8080/api/instances/<instance_id>/tasks/<task>/session?attempt_id=<attempt_id>'
```

看板里点执行记录的「回放会话」即可打开对应轮次的对话。

查看单个任务的执行历史：

```bash
curl -s 'http://127.0.0.1:8080/api/instances/<instance_id>/tasks/<task>/attempts'
```

返回里每个 attempt 带 `state`、`started_at`、`ended_at`、`duration_ms`、
`provider`、`session_id`、`failure_reason`、`output_excerpt`。
看板的任务页在顶部「执行历史」面板里展示同一份数据。

查看任务会话事件：

```bash
curl -s 'http://127.0.0.1:8080/api/sessions/hello-001/hello?limit=200'
```

查看人工消息：

```bash
curl -s http://127.0.0.1:8080/api/workflow/hello-001/task/hello/messages
```

排队补充要求：

```bash
curl -X POST \
  http://127.0.0.1:8080/api/workflow/hello-001/task/hello/messages \
  -H 'Content-Type: application/json' \
  -d '{"actor":"human:alice","mode":"queue","message":"请补充错误处理并重新验证"}'
```

中断当前 turn 并立即调整：

```bash
curl -X POST \
  http://127.0.0.1:8080/api/workflow/hello-001/task/hello/messages \
  -H 'Content-Type: application/json' \
  -d '{"actor":"human:alice","mode":"interrupt","message":"停止当前方案，改用 JWT"}'
```

消息状态依次为 `PENDING → PROCESSING → APPLIED`。被新 interrupt 替代的消息为
`INTERRUPTED`，执行异常则为 `FAILED`。

## 实例任务：retry 与 rewind

作业流实例有两个不同的重跑入口，不要混用：

| 操作 | 语义 | 下游行为 |
|------|------|----------|
| `retry` | 只重跑当前节点 | 未完成 / `SKIPPED_UPSTREAM_FAILED` 的下游自动复活；已 `DONE` 的不动 |
| `rewind` | 从当前节点重新开始 | 下游闭包（含已 `DONE`）全部重置 |

只修了代码或产物、输入契约没变，用 retry：

```bash
curl -X POST \
  http://127.0.0.1:8080/api/instances/inst-001/tasks/test/retry
```

`do` / `input` / `output` 变了，或需求变了需要从某一步重来，用 rewind：

```bash
curl -X POST \
  http://127.0.0.1:8080/api/instances/inst-001/tasks/test/rewind \
  -H 'Content-Type: application/json' \
  -d '{"reason":"验收标准变更","reset_downstream":true}'
```

rewind 的行为：

1. 目标节点按依赖状态进入 `PENDING` 或 `BLOCKED`；下游闭包全部 `BLOCKED`；
2. 闭包内任务的 attempts、产物、结论、会话绑定先归档到
   `jobflows/instances/<id>/rewinds/<rewind_id>/`，再清除活动字段；
3. `lease_epoch` 递增，旧 attempt 的迟到结果会被拒绝；
4. 已结束的实例回到 `RUNNING`，聚合器下一轮自动继续调度，不需要额外触发。

`reset_downstream=false` 时只重置目标节点，适合"这一节点自身实现有问题、
下游结论仍然有效"的场景。`SUPERSEDED`、`DRAINING`、`ARCHIVED` 实例不允许
rewind；模板结构整体变化请用 `rerun` 创建新实例。

## 当前边界

- Dashboard 使用 3 秒短轮询，不是 SSE/WebSocket 推送。
- `actor` 由调用方提交；WebAPI 尚未提供身份认证和角色权限，生产环境必须在
  反向代理或网关层补充认证。
- `interrupt` 在 ACP 请求的安全边界生效，不能回滚 Agent 已经完成的外部副作用。
- 人工直接修改代码时，应先完成修改，再发送说明；Agent 会在新 turn 中检查并重新验证。
