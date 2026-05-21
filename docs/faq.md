# FAQ

## Aggregator 和 Watchdog 可以合并吗？

技术上可以，但**不应该合并**。以下是原因：

### 1. 不同的轮询频率

这是最实际的阻碍：

| 组件 | 默认间隔 | CLI 参数 |
|------|---------|---------|
| Aggregator | 5s | `--aggregator-interval` |
| Watchdog | 30s | `--watchdog-interval` |

- Aggregator 需要快速响应：依赖满足后尽快激活下游任务，减少 pipeline 空转时间
- Watchdog 可以慢速轮询：僵尸检测对延迟不敏感，Agent 死亡/超时的检测晚几十秒不影响

合并后只能用同一个频率——快了浪费（watchdog 空转），慢了延迟（聚合推进变慢）。

### 2. 状态流向相反

它们操作的是状态机中**不同的状态子集**，语义上天然正交：

| 组件 | 读取的任务状态 | 写入的状态 | 方向 |
|------|-------------|-----------|------|
| Aggregator | BLOCKED / "" | → PENDING | **向前**推进 |
| Watchdog | IN_PROGRESS | → PENDING（回滚）或 FAILED（终止） | **向后**回收或**终止** |

### 3. 关注点分离

Watchdog 承载了聚合器不需要的额外复杂度：

- Agent 健康检查（查 Consul Service Health）
- 超时计算（ISO 时间解析 + 差值计算）
- 重试计数器 + 最大重试上限逻辑
- 告警写入（`alerts/` 路径的 JSON）

把这些塞进 Aggregator 会让简单的依赖推进逻辑被杂音淹没。

### 4. 故障隔离

当前是两个独立线程，一个崩溃不影响另一个。合并后如果 watchdog 部分有 bug 抛异常，可能拖慢或打断 aggregator 的推进逻辑。

### 5. 独立开关

`daemon.py` 提供 `--no-aggregator` / `--no-watchdog` 标志，可以在调试或部署专用实例时独立关闭。合并后失去这种灵活性。

### 6. 独立测试

现在可以分别写 `test_aggregator.py` 和 `test_watchdog.py`，测试数据和场景完全解耦。合并后测试用例组合爆炸。

---

> **结论：** 它们是同一枚硬币的两面（一个向前推进、一个向后回收），但应该保持独立——不同的频率、不同的关注点、不同的故障域。合并带来的耦合会超过节省的那几行代码的价值。
