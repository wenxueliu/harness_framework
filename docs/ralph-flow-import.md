# 导入 ralph-flow 工作流

把 `services/ralph-flow` 的 YAML 工作流降级成作业流模板。ralph-flow 是 DAG 的
子集：步骤之间靠 `on_pass` 串起来，多出来的只有**每步的 check**——而 check 正好
可以变成一个独立节点。所以这里只做**适配**，不引入第二套编排。

## 接口

看板「新建作业流模板」页右上角有「导入 ralph-flow」按钮，选 `.yaml` / `.yml` /
`.json` 后创建模板并跳到编辑页。也可以直接调接口：

```bash
curl -X POST localhost:8080/api/templates/import-ralph-flow \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "spec",
    "group_id": "grp_xxx",
    "agent": "opencode",
    "check_agent": "claude",
    "yaml": "<spec.yaml 文件内容>"
  }'
```

`source` / `workflow` 也可以替代 `yaml`；内容为 JSON 时不需要 PyYAML。

## 降级规则

每个 ralph 步骤变成两个 DAG 节点：

```text
explore ──▶ explore__check ──▶ propose ──▶ propose__check ──▶ implement …
   ▲              │
   └──────────────┘   on_fail 回退 + max_fail_count
```

| ralph-flow | 作业流模板 |
|------------|-----------|
| `steps[].id` | 任务 `<id>`（执行） |
| `steps[].do` | 任务 `<id>` 的 `do` |
| `steps[].input` / `output` | 任务 `<id>` 的 `input` / `output` |
| `steps[].check` / `check_voting` | 任务 `<id>__check`（验证），`do` = 验收标准 |
| 验证结论 | 验证节点的 `output` + `output_format`（marker: `VERDICT: PASS`） |
| `on_pass` | 下一个执行节点的 `depends_on` |
| `on_fail` | 验证节点上的 `on_fail: {task, max_fail_count}`（回退到执行节点） |
| `max_fail_count` | 回退次数上限 |
| `manual_step` | 验证节点 `require_approval: true` |
| `check_voting` | N 个并行验证节点，下一节点依赖全部（AND 合流 = 全票通过） |
| 失败预算 | 同一 `fail_group`（= 步骤）的验证者共享一个计数，对应 ralph 的步骤级 `fail_count` |
| `reset` / `auto_reset` | 每个节点本身是新会话，天然满足 |
| `adversarial_check.agent` | 验证节点用独立 agent（claude/codex/opencode） |
| `adversarial_check.system_prompt` | 并入验证节点的 `do` |
| `adversarial_check.timeout_ms` | 记录在 `ralph_flow` 元数据里（框架暂未按节点覆盖超时） |

节点里**不留任何 ralph 专用字段**，语义全部落在 `do` / `input` / `output` /
`output_format` / `on_fail` / `require_approval` / `fail_group` 上。
溯源信息放在**模板级** `provenance`（含原始 steps，足以还原成 ralph YAML），
它不会进节点、也不会进提示词 JSON，Agent 只看契约字段，不存在两个真相来源。

## 框架原生语义（新增）

| 任务字段 | 含义 | 实现位置 |
|----------|------|----------|
| `require_verdict` | 结论必须是 `VERDICT: PASS`，否则任务判失败 | `acp_dispatcher._execute` |
| `require_approval` | 需要人工批准才执行；批准只对本次有效 | `acp_dispatcher._claim` |
| `on_fail` | 失败时回退到指定任务重跑 | `aggregator._recover_failed_checks` |
| `retry_feedback` | 回退时把验证失败原因与证据带给目标节点 | `aggregator` 写入 / `acp_dispatcher._build_prompt` 注入 |

验证节点没给出 `VERDICT: PASS` → 产物格式校验失败 → 任务失败 → 触发 `on_fail`
回退到对应的执行节点重跑（最多 `max_fail_count` 次）。

**多验证者语义**（对应 ralph-flow 的 `check_voting`）

- 全过才放行：后继节点依赖全部验证节点（DAG AND 合流），任一验证失败就回退重跑
- **并行执行**：N 个验证节点同时被调度（受 `ACP_MAX_CONCURRENCY` 限制），与 ralph 的并行投票一致
- 失败预算按**步骤**共享：同一 `fail_group` 的 N 个验证者共用一个计数（`tasks/<step>/fail_count`），
  **一轮失败只记一次**（与该轮几个验证者失败无关），与 ralph 的步骤级 `fail_count` 一致
- 验证通过后清零该步骤的预算
- 在**框架**的 DAG 语义里，每个验证节点是独立节点，理论上可以指向不同后继；
  但**同一 `fail_group` 的节点必须用相同的 `on_fail` 目标与预算**（模板校验强制），
  否则多个验证者会同时重置不同的上游节点，造成并行写入冲突

**独立验证者**：工作流带 `adversarial_check` 时，验证节点会带上
`acp.permission_policy = "deny_edit"` —— 验证者可以读文件、跑验证命令，但不能改动
工作区（与 ralph-flow 的 `edit: deny` 对齐）。框架的第三种权限策略：

| 策略 | 行为 |
|------|------|
| `allow_once` | 每次询问都允许一次（默认） |
| `deny` | 拒绝一切权限请求 |
| `deny_edit` | 拒绝改文件/删除/移动，允许执行命令 |

验证节点的提示词要求最后一行输出：

```text
VERDICT: PASS
VERDICT: FAIL <原因>
```

没有结论一律按失败处理。

人工批准：

```bash
curl -X POST localhost:8080/api/instances/<instance_id>/tasks/<task_id>/approve
```

任务在 `AWAITING_REVIEW` 状态时可用；批准后回到 `PENDING` 等待调度。
验证重跑时批准会被消费，需要重新批准（对应 ralph-flow 的“审查门重新武装”）。

## 限制

- **不能有真正的分支选择**：多个步骤 `on_pass` 到同一目标会被拒绝（DAG 是 AND 合流）
- **回退只能回到已存在的任务节点**：`on_fail` 指向不存在或更靠后的任务会被忽略
- **子工作流**（`workflow:` 形式的步骤）不支持导入
- 验证节点与执行节点默认用同一个 agent，可用 `check_agent` 指定独立验证者

## 相关

- 任务模型与提示词结构：[按任务选择模型与会话](task-model-execution.md)
- ralph-flow 原始定义：`services/ralph-flow/workflows/`
