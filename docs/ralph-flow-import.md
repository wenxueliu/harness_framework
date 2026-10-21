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
| `steps[].do` + `input` + `output` | 任务 `<id>` 的 `description` |
| `steps[].check` / `check_voting` | 任务 `<id>__check`（验证），`check_criteria` |
| `on_pass` | 下一个执行节点的 `depends_on` |
| `on_fail` | 验证节点上的 `on_fail: {task, max_fail_count}` |
| `max_fail_count` | 回退次数上限 |
| `manual_step` | 验证节点 `require_approval: true` |
| `check_voting` | N 个并行验证节点，下一节点依赖全部（AND 合流 = 全票通过） |
| `reset` / `auto_reset` | 每个节点本身是新会话，天然满足 |
| `adversarial_check` | 记录在 `ralph_flow` 元数据里 |

## 框架原生语义（新增）

| 任务字段 | 含义 | 实现位置 |
|----------|------|----------|
| `require_verdict` | 结论必须是 `VERDICT: PASS`，否则任务判失败 | `acp_dispatcher._execute` |
| `require_approval` | 需要人工批准才执行；批准只对本次有效 | `acp_dispatcher._claim` |
| `on_fail` | 失败时回退到指定任务重跑 | `aggregator._recover_failed_checks` |

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
