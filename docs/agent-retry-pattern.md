# Agent 通用修复重试模式

## 背景

框架早期实现了 Aggregator 内的 feedback 重测逻辑（检查 `feedback/<service>/status == FIXED` 后触发重测）。经过讨论，决定将此逻辑从 Aggregator 移出，由 Agent 自行通过 Message Bus 管理。

## 决策

### 1. 移除 Aggregator 中的重测逻辑

**原因**：
- Aggregator 的定位是"状态驱动的 DAG 调度器"，不应感知业务语义
- 重测流程需要知道"哪些服务需要修复"、"修复消息的 msg_id"，这些是任务级别的信息
- 保留 feedback 状态只是增加了中间层抽象

**结果**：
- 移除 `aggregator.py` 中的 `_maybe_retest` 方法（40 行）
- 重测逻辑由 Agent 自行实现

### 2. 所有 Agent 共享通用修复重试模式

这不是 Test Agent 独有的模式，而是**所有 Agent** 的通用行为：

```
任何 Agent 在调用下游服务时都可能失败
    ↓
失败后识别需要修复的服务
    ↓
通过 Message Bus 发送 FIX 消息
    ↓
轮询消息状态，等待所有修复完成
    ↓
重试（上限 3 次）
    ↓
成功则继续，失败则标记任务 FAILED
```

### 3. 使用 Message Bus 作为修复通信机制

**优势**：
- 消息持久化在 Consul KV，不会丢失
- 完整的状态流转（PENDING → PROCESSING → DONE/FAILED/TIMEOUT）
- 统一的通信接口，所有 Agent 共用

**废弃**：`feedback/<service>/status` 字段，不再使用

## 通用 Agent 行为模式

```python
class BaseAgent:
    MAX_RETRIES = 3

    def run_task(self) -> None:
        """所有任务的通用入口"""
        self.claim_task()  # 领取 → 写入 IN_PROGRESS

        while self.retry_count <= self.MAX_RETRIES:
            result = self.execute()  # 子类实现具体逻辑

            if result.success:
                self.mark_done()
                return

            failed_services = self.identify_failed_services(result)
            if not failed_services:
                self.mark_failed()
                return

            msg_ids = self.send_fix_requests(failed_services, result.details)
            self.wait_for_fixes(msg_ids)

            self.retry_count += 1

        self.mark_failed()

    def send_fix_requests(self, services: list[str], details: dict) -> list[tuple[str, str]]:
        """发送 FIX 消息到各服务，返回 (service, msg_id) 列表"""
        msg_ids = []
        for svc in services:
            msg = self.message_bus.send(
                req_id=self.req_id,
                from_task=self.task_name,
                to_task=svc,
                action="FIX",
                params={"failure_details": details},
                timeout=600  # 10 分钟修复超时
            )
            msg_ids.append((svc, msg.msg_id))
        return msg_ids

    def wait_for_fixes(self, msg_ids: list[tuple[str, str]]) -> None:
        """轮询消息状态，直到所有消息 DONE/FAILED/TIMEOUT"""
        pending = set(msg_ids)

        while pending:
            for svc, msg_id in list(pending):
                msg = self.message_bus.get(self.req_id, svc, msg_id)
                if msg.status in (MessageStatus.DONE, MessageStatus.FAILED, MessageStatus.TIMEOUT):
                    pending.remove((svc, msg_id))

            if pending:
                time.sleep(5)

    def execute(self) -> Result:
        raise NotImplementedError  # 子类实现

    def identify_failed_services(self, result: Result) -> list[str]:
        raise NotImplementedError  # 子类实现
```

### 具体 Agent 示例

```python
class TestAgent(BaseAgent):
    """测试任务：执行测试套件"""

    def execute(self) -> TestResult:
        return run_test_suite(self.req_id)

    def identify_failed_services(self, result: TestResult) -> list[str]:
        return result.failed_services  # 从测试报告提取


class FrontendAgent(BaseAgent):
    """前端任务：调用后端 API"""

    def execute(self) -> CallResult:
        return self.call_backend_apis()

    def identify_failed_services(self, result: CallResult) -> list[str]:
        return result.failed_endpoints  # 从 API 调用错误提取
```

## 消息类型

Message Bus 的 `action` 字段可以扩展：

| action | 用途 | 触发场景 |
|--------|------|---------|
| `FIX` | 修复 bug | API 返回错误、测试失败 |
| `IMPLEMENT` | 实现缺失功能 | API 未实现（404） |
| `RETRY` | 重试请求 | 服务暂时不可用 |

## WebAPI 可观测性

Agent 在等待修复期间可以写入进度状态：

```python
# 发送 FIX 消息后写入进度
for svc, _ in msg_ids:
    consul.kv_put(f"workflows/{req_id}/context/fix_progress/{svc}", "PROCESSING")

# 修复完成后
consul.kv_put(f"workflows/{req_id}/context/fix_progress/{svc}", "DONE")
```

WebAPI 可以展示：
```
test: IN_PROGRESS (WAITING_FIX: [service_A, service_B])
  ↳ service_A: PROCESSING
  ↳ service_B: DONE
```

## 与 DAG 调度的关系

- **Aggregator**：只负责 DAG 激活（依赖 DONE → 下游 PENDING）
- **Agent**：负责具体任务执行，包含失败重试逻辑
- **Watchdog**：只负责 Agent 存活和任务超时检测，不参与业务重试

职责分离清晰，每层只做一件事。

## 相关文档

- [message-bus.md](./message-bus.md) - Message Bus 详细说明