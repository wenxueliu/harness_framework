# 动态任务提案协议（Proposal Protocol）

## 背景

框架通过 DAG 定义任务依赖，但实际执行中可能出现需要新增任务的场景：

- 测试发现规划阶段遗漏的工作（如性能优化、安全修复）
- 需要引入新服务处理额外需求
- 人工临时追加任务

## 设计原则

1. **框架提供机制，人工决策**：框架提供 Proposal 状态和检查能力，是否采纳由人工决定
2. **不中断正在执行的任务**：默认等待当前任务完成后自然衔接
3. **CAS 保证原子性**：多个 Agent 同时提出提案时，只有一个能成功设置 Proposal 状态

## 协议流程

```
Agent 发现遗漏 ──→ 提出新任务 ──→ status = Proposal ──→ 暂停调度
                                                         │
人工审查 ◄────────────────────────────────────────────────┘
    │
    ├── 确认 ──→ status = CONFIRMED ──→ 新任务激活
    │
    └── 拒绝 ──→ 从 dependencies 删除提案 ──→ status = CONFIRMED
                                                      ──→ Agent 可重新发起
```

## Agent 侧操作

### 1. 检查需求状态

```bash
STATUS=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status?raw")
```

### 2. 提出新任务

```bash
# 1. 读取当前 dependencies
DEPS=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/dependencies?raw")

# 2. 添加新任务定义
echo "$DEPS" | python3 -c "
import sys, json
deps = json.load(sys.stdin)
deps['new-task-name'] = {
    'type': 'backend',          # design | review | backend | test | deploy
    'depends_on': ['task-a'],
    'proposed_by': '$TASK_NAME',  # 提出者，用于追溯
    'reason': '测试发现遗漏的安全检查',
    'description': '补充 XSS 过滤逻辑'
}
print(json.dumps(deps, ensure_ascii=False))
" > /tmp/new_deps.json

# 3. 写回 dependencies
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/dependencies" -d @/tmp/new_deps.json

# 4. CAS 设置 Proposal（若已有 Proposal 则跳过）
INDEX=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status" | \
  python3 -c "import sys,json; print(json.load(sys.stdin)[0]['ModifyIndex'])")
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status?cas=$INDEX" -d "Proposal"

# 5. 写入提案元数据
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/new-task/proposed_by" -d "$TASK_NAME"
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/tasks/new-task/proposed_at" \
  -d "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

### 3. 等待提案解决

```bash
while true; do
  STATUS=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status?raw")
  if [ "$STATUS" != "Proposal" ]; then
    echo "提案已解决: $STATUS"
    break
  fi
  sleep 5
done
```

### 4. 退出当前任务

提案提交后，Agent 应正常结束当前任务（写入 DONE 或 AWAITING_REVIEW），而非阻塞等待。

## 人工侧操作

### 查看待确认提案

```bash
curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/dependencies?raw" | python3 -c "
import sys, json
deps = json.load(sys.stdin)
for task, info in deps.items():
    if info.get('proposed_by'):
        print(f\"任务: {task}\")
        print(f\"  提出者: {info['proposed_by']}\")
        print(f\"  类型: {info.get('type')}\")
        print(f\"  依赖: {info.get('depends_on', [])}\")
        print(f\"  原因: {info.get('reason', 'N/A')}\")
        print()
"
```

### 确认全部提案

```bash
INDEX=$(curl -s "http://$CONSUL_ADDR/v1/kq/workflows/$REQ_ID/status" | \
  python3 -c "import sys,json; print(json.load(sys.stdin)[0]['ModifyIndex'])")
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status?cas=$INDEX" -d "CONFIRMED"
```

### 拒绝指定提案

```bash
DEPS=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/dependencies?raw")
echo "$DEPS" | python3 -c "
import sys, json
deps = json.load(sys.stdin)
deps.pop('new-task', None)  # 删除被拒绝的任务
print(json.dumps(deps, ensure_ascii=False))
" > /tmp/deps_filtered.json

curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/dependencies" -d @/tmp/deps_filtered.json
INDEX=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status" | \
  python3 -c "import sys,json; print(json.load(sys.stdin)[0]['ModifyIndex'])")
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status?cas=$INDEX" -d "CONFIRMED"
```

## Aggregator 行为

```python
def _process_requirement(self, req_id):
    status, _ = self.consul.kv_get(f"workflows/{req_id}/status")

    if status == "DRAFT":
        return  # 不调度

    if status == "Proposal":
        return  # 暂停调度，等待人工确认

    # CONFIRMED / IN_PROGRESS: 正常调度
    self._activate_tasks(req_id)
```

## Consul KV 变更

| 操作 | KV 路径 | 值 |
|------|---------|-----|
| 提出任务 | `workflows/<req_id>/dependencies` | 添加新 task 到 JSON |
| 标记提案 | `workflows/<req_id>/status` | `Proposal` |
| 提案元数据 | `workflows/<req_id>/tasks/<task>/proposed_by` | 提出者 |
| 提案时间 | `workflows/<req_id>/tasks/<task>/proposed_at` | ISO 时间 |
| 人工确认 | `workflows/<req_id>/status` | `CONFIRMED` |
| 删除提案 | `workflows/<req_id>/dependencies` | 从 JSON 中移除 |

## 与 Message Bus 的关系

- **Proposal 协议**：解决"任务 DAG 不完整"的问题（需要人工决策）
- **Message Bus**：解决"任务间需要传递上下文"的问题（自动流转）

两者独立使用，不互相依赖。

## 相关文档

- [status-state-machine.md](./status-state-machine.md) — 状态机完整定义
- [dynamic-tasks.md](./dynamic-tasks.md) — 动态任务创建设计文档
- [message-bus.md](./message-bus.md) — 任务间消息通信
