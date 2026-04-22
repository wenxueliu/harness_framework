# 动态任务创建机制

## 背景

框架通过 DAG 定义任务依赖，但实际执行中可能出现需要新增任务的场景：
- 测试发现规划阶段遗漏的工作（如性能优化、安全修复）
- 需要引入新服务处理额外需求

## 设计原则

- **框架提供机制，人工决策**：框架提供 Proposal 状态和检查能力，是否中断运行中的 Agent 由人工通过 ACP 协议决定
- **简化实现**：当前不考虑强制中断场景，默认等 Agent 执行完成后自然衔接

## 需求状态机

```
DRAFT → CONFIRMED → IN_PROGRESS → DONE
         ↑
       Proposal（中间态）
```

| 状态 | 含义 | Aggregator 行为 |
|------|------|----------------|
| DRAFT | 草稿，尚未启动 | 不调度 |
| Proposal | 有 Agent 发现需要新任务 | 暂停调度，等待人工确认 |
| CONFIRMED | 人工确认后 | 正常调度 |
| IN_PROGRESS | 执行中 | 正常调度 |
| DONE | 全部完成 | - |

## 流程

### 1. Agent 发现需要新任务

```python
# Agent 在开始时或写完之后检查状态
def check_proposal_status(req_id):
    status, _ = consul.kv_get(f"workflows/{req_id}/status")
    return status == "Proposal"

def propose_new_task(req_id, task_name, task_def):
    # 1. 更新 dependencies
    deps = json.loads(consul.kv_get(f"workflows/{req_id}/dependencies"))
    deps[task_name] = task_def
    consul.kv_put(f"workflows/{req_id}/dependencies", json.dumps(deps))

    # 2. 设置为 Proposal（CAS 保证只有一个）
    current, idx = consul.kv_get(f"workflows/{req_id}/status")
    if current != "Proposal":
        consul.kv_put(f"workflows/{req_id}/status", "Proposal", cas=idx)

    # 3. 通知人工
    notify_human_review(req_id)

    # 4. 退出当前任务
    return "proposal_pending"
```

### 2. Aggregator 行为

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

### 3. 人工确认

```python
# POST /api/workflow/<req_id>/status
{
    "action": "confirm" | "reject",
    "accepted_tasks": ["perf-opt"],  # 可选，人工筛选接受哪些
    "rejected_tasks": ["sec-fix"]    # 可选，拒绝哪些
}
```

- **confirm**：将 accepted_tasks 合并到 dependencies，状态改为 CONFIRMED
- **reject**：从 dependencies 删除 rejected_tasks，状态改为 CONFIRMED（让 Agent 重新发起）

### 4. 无 interrupt 的自然衔接

默认场景（不强制中断）：

```
1. Agent 发现需要新任务，设置 status=Proposal，退出
2. 人工确认，status 改为 CONFIRMED
3. 运行中的 Agent 继续执行完毕
4. 所有 Agent 不再执行时，更新后的 dependencies 自然生效
5. 新任务按依赖被激活执行
```

## Agent 检查机制

Agent 需要在适当时机检查需求状态：

```python
class BaseAgent:
    def run(self):
        # 开始时检查
        if self._is_proposal():
            self._wait_proposal_resolved()

        # 执行任务...

        # 写完之后检查（如果任务中发现需要新任务）
        if self._needs_new_task():
            self._propose_task(new_task_def)
            # 退出
            return

    def _is_proposal(self) -> bool:
        status, _ = self.consul.kv_get(f"workflows/{self.req_id}/status")
        return status == "Proposal"

    def _wait_proposal_resolved(self):
        """等待 Proposal 被解决（人工确认或拒绝）"""
        while True:
            status, _ = self.consul.kv_get(f"workflows/{self.req_id}/status")
            if status != "Proposal":
                return
            time.sleep(5)
```

## Agent Skill

框架为 Agent 提供一组 skill，用于检查需求状态和提出新任务。

### Skill 定义

| Skill | 用途 | 参数 |
|-------|------|------|
| `check_workflow_status` | 检查需求当前状态 | req_id |
| `wait_for_proposal` | 等待 Proposal 被解决 | req_id, timeout |
| `propose_task` | 提出新的子任务 | req_id, task_name, task_def |
| `list_pending_proposals` | 查看当前所有待确认的提案 | req_id |

### Skill 实现

```python
class WorkflowSkills:
    """Agent 可调用的 workflow 相关 skill"""

    def __init__(self, consul: ConsulClient):
        self.consul = consul

    def check_workflow_status(self, req_id: str) -> str:
        """检查需求当前状态"""
        status, _ = self.consul.kv_get(f"workflows/{req_id}/status")
        return status or "DRAFT"

    def wait_for_proposal(self, req_id: str, timeout: int = 3600) -> dict:
        """等待 Proposal 被解决（人工确认或拒绝）

        Args:
            req_id: 需求 ID
            timeout: 超时时间（秒），默认 1 小时

        Returns:
            {"resolved": True, "status": "CONFIRMED"}  # 或 超时
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, _ = self.consul.kv_get(f"workflows/{req_id}/status")
            if status != "Proposal":
                return {"resolved": True, "status": status}
            time.sleep(5)

        return {"resolved": False, "reason": "timeout"}

    def propose_task(self, req_id: str, task_name: str, task_def: dict) -> dict:
        """提出新的子任务

        Args:
            req_id: 需求 ID
            task_name: 新任务名称
            task_def: 任务定义（type, depends_on, proposed_by 等）

        Returns:
            {"success": True, "status": "Proposal"}  # 或 已被其他提案抢先
        """
        # 1. 更新 dependencies
        deps_str, _ = self.consul.kv_get(f"workflows/{req_id}/dependencies")
        deps = json.loads(deps_str) if deps_str else {}

        if task_name in deps:
            return {"success": False, "reason": "task already exists"}

        deps[task_name] = task_def
        self.consul.kv_put(f"workflows/{req_id}/dependencies", json.dumps(deps))

        # 2. 设置为 Proposal（CAS 保证原子性）
        current, idx = self.consul.kv_get(f"workflows/{req_id}/status")
        if current == "Proposal":
            return {"success": True, "status": "Proposal", "already_proposed": True}

        success = self.consul.kv_put(
            f"workflows/{req_id}/status", "Proposal", cas=idx
        )
        if not success:
            return {"success": False, "reason": "concurrent proposal detected"}

        # 3. 记录新任务元数据
        self.consul.kv_put(
            f"workflows/{req_id}/tasks/{task_name}/proposed_by",
            task_def.get("proposed_by", "unknown")
        )
        self.consul.kv_put(
            f"workflows/{req_id}/tasks/{task_name}/proposed_at",
            _now_iso()
        )

        return {"success": True, "status": "Proposal"}

    def list_pending_proposals(self, req_id: str) -> list[dict]:
        """查看当前待确认的提案"""
        deps_str, _ = self.consul.kv_get(f"workflows/{req_id}/dependencies")
        deps = json.loads(deps_str) if deps_str else {}

        proposals = []
        for task_name, task_def in deps.items():
            if "proposed_by" in task_def or "proposed_at" in task_def:
                proposals.append({
                    "task_name": task_name,
                    "proposed_by": task_def.get("proposed_by"),
                    "proposed_at": task_def.get("proposed_at"),
                    "depends_on": task_def.get("depends_on", []),
                    "type": task_def.get("type", "task"),
                })

        return proposals


def _now_iso() -> str:
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"
```

### Agent 使用示例

```python
class TestAgent:
    def __init__(self, req_id: str, task_name: str, skills: WorkflowSkills):
        self.req_id = req_id
        self.task_name = task_name
        self.skills = skills

    def run(self) -> None:
        # 1. 开始时检查 Proposal 状态
        status = self.skills.check_workflow_status(self.req_id)
        if status == "Proposal":
            result = self.skills.wait_for_proposal(self.req_id)
            if not result["resolved"]:
                # 超时处理
                return

        # 2. 执行测试
        test_result = execute_tests()

        # 3. 如果发现需要新任务，提出提案
        if test_result.need_new_task:
            resp = self.skills.propose_task(
                req_id=self.req_id,
                task_name=test_result.new_task_name,
                task_def={
                    "type": "task",
                    "depends_on": test_result.depends_on,
                    "proposed_by": self.task_name,
                    "reason": test_result.reason,
                }
            )
            return  # 退出，等人工确认

        # 4. 正常完成
        self.consul.kv_put(f"workflows/{self.req_id}/tasks/{self.task_name}/status", "DONE")
```

### Skill 注册机制

框架可以提供 skill 注册接口，让 Agent 发现可用的 skill：

```python
# Agent 初始化时获取可用 skills
skills = framework.get_skills(agent_type="test")
# 返回 WorkflowSkills 实例，包含 check_workflow_status, propose_task 等
```

## ACP 协议（未来扩展）

人工可以通过 ACP 协议决定是否中断运行中的 Agent：

- **中断**：停止目标 Agent，状态回退，从 CONFIRMED 重新调度
- **不中断**：等 Agent 执行完毕，自然衔接新任务

当前简化实现，假设使用"不中断"模式。

## Consul KV 结构

```
workflows/<req_id>/
├── status                 # DRAFT | Proposal | CONFIRMED | IN_PROGRESS | DONE
├── dependencies          # 包含所有任务（已确认 + 待确认）
├── tasks/<name>/
│   ├── status
│   ├── proposed_by       # 如果是新任务，记录提出者
│   └── proposed_at       # 提出时间
└── tasks/<name>/...
```

## 相关文档

- [message-bus.md](./message-bus.md) - 任务间消息通信
- [agent-retry-pattern.md](./agent-retry-pattern.md) - 任务间失败处理机制