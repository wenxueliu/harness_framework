# 操作参考

常见操作的命令参考。概念解释见 [concepts.md](concepts.md)。

## Agent 工作流

### 启动 Agent（Consul / --local 模式）

```bash
export CONSUL_ADDR=127.0.0.1:8500
export AGENT_ID=my-agent
export SERVICE_NAME=user-service
export REPO_PATH=/path/to/your/service

# 后台心跳
python scripts/heartbeat.py --loop 10 &
```

### 抢占任务

```bash
python scripts/claim_task.py req-001 design-api
# 或自动抢占
python scripts/claim_next_task.py --loop
```

### 执行与完成

```bash
python scripts/log_step.py req-001 "开始实现 API"
python scripts/complete_task.py req-001 design-api \
  --meta '{"branch":"feature/login","commit":"abc123"}'
```

### 任务失败

```bash
python scripts/fail_task.py req-001 build-backend \
  --error "数据库连接失败" \
  --retry-hint retry
```

## 测试失败与重测

测试 Agent 发现失败后，通过 Message Bus 通知修复，修复完成后自动重测。

```bash
# Test Agent：发送 FIX 消息
python scripts/message_send.py req-001 build-user-service fix \
  --params '{"error": "登录接口返回 500", "severity": "high"}'

# 标记失败
python scripts/fail_task.py req-001 test-e2e --error "登录接口返回 500"

# 服务 Agent：监听并修复
python scripts/message_poll.py req-001 --task build-user-service --status PENDING
python scripts/message_complete.py req-001 <msg_id> \
  --task build-user-service \
  --result '{"fixed": true, "commit": "abc123"}'
```

重测由 Test Agent 自己管理：失败 → 发 FIX → 等所有 FIX 完成 → 重测（最多 3 次）。

> 详见 [message-bus.md](message-bus.md)

## 动态任务提案

Agent 发现遗漏任务时：

```bash
# 读 deps → 添加新任务 → CAS 设置 Proposal
INDEX=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status" | \
  python3 -c "import sys,json; print(json.load(sys.stdin)[0]['ModifyIndex'])")
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status?cas=$INDEX" -d "Proposal"
```

人工确认：

```bash
INDEX=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status" | \
  python3 -c "import sys,json; print(json.load(sys.stdin)[0]['ModifyIndex'])")
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/$REQ_ID/status?cas=$INDEX" -d "CONFIRMED"
```

> 详见 [proposal-protocol.md](proposal-protocol.md)

## 人工干预

```bash
# 暂停
curl -X PUT "http://127.0.0.1:8500/v1/kv/workflows/req-001/control" -d "PAUSE"

# 恢复
curl -X DELETE "http://127.0.0.1:8500/v1/kv/workflows/req-001/control"

# 中止
curl -X PUT "http://127.0.0.1:8500/v1/kv/workflows/req-001/control" -d "ABORT"

# 重试失败任务
curl -X PUT "http://127.0.0.1:8500/v1/kv/workflows/req-001/tasks/backend/status" -d "PENDING"
```

也可通过 WebAPI 操作：

```bash
curl -X POST "http://127.0.0.1:8080/api/workflow/req-001/control" \
  -H "Content-Type: application/json" \
  -d '{"action": "RETRY", "task_name": "backend"}'
```

## 常见问题

**Q: Agent 抢占任务失败？**
- 检查任务状态是否为 `PENDING`
- 检查 `assigned_agent` 是否已被抢占

**Q: 心跳失败 404？**
- Agent 已被自动注销，需要重新注册
- 单机模式（`--local-file` / `--standalone`）下无需心跳

**Q: Aggregator 不调度任务？**
- 检查 `published` 是否为 `true`
- 检查 `control` 是否为 `PAUSE` 或 `ABORT`

**Q: Watchdog 频繁回滚任务？**
- 检查 Agent 是否存活
- 检查 `task_timeout` 是否设得太短
- 单机模式下，确认正在使用默认 Agent ID 或已正确注册
