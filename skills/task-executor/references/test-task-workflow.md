# Test Task Workflow

E2E 测试任务执行流程: 运行测试 → 发现失败 → 发送 FIX 消息 → 等待修复 → 重测。

## 流程总览

```
Step 1: 准备测试环境
Step 2: 运行 E2E 测试
Step 3: 结果分析
Step 4: 发送 FIX 消息（如有失败）
Step 5: 等待修复完成
Step 6: 重测（最多 3 轮）
Step 7: 报告结果
```

## Step 1: 准备测试环境

### 1.1 加载测试用例

```bash
# 从任务元数据获取 E2E 用例
python3 skills/stage-bridge/scripts/read_context.py <req_id> e2e_test_cases
```

### 1.2 部署测试环境

- 启动/确认被测服务已部署
- 准备测试数据
- 设置环境变量

## Step 2: 运行 E2E 测试

运行 E2E 测试套件（Playwright / Cypress / Selenium 等）。

```bash
python3 skills/stage-bridge/scripts/log_step.py <req_id> "<task_name>" \
  --type "TEST_RUN" --message "开始 E2E 测试"
```

## Step 3: 结果分析

### 3.1 全部通过

```
记录: "[E2E-PASS] {N}/{N} E2E 用例通过"
→ 标记任务 DONE
```

### 3.2 有失败

对每个失败用例:
1. 分析失败原因
2. 归因到具体服务
3. 提取错误上下文（日志、截图、请求/响应）

```
记录: "[E2E-FAIL] {F}/{N} 失败, 归因: {service_a}: {reason}, {service_b}: {reason}"
```

## Step 4: 发送 FIX 消息

对每个需要修复的服务，通过 Message Bus 发送 FIX 消息:

```bash
python3 skills/stage-bridge/scripts/message_send.py <req_id> <service_name> \
  --type "FIX" \
  --data '{
    "failed_test": "E2E-LOGIN-001",
    "endpoint": "POST /api/v1/auth/login",
    "error": "HTTP 500 Internal Server Error",
    "expected": "HTTP 200 + JWT tokens",
    "log_snippet": "...",
    "resolved_by": "<ISO timestamp + TTL>"
  }'
```

## Step 5: 等待修复

### 5.1 轮询消息状态

```bash
python3 skills/stage-bridge/scripts/message_poll.py <req_id> <task_name>
```

### 5.2 标记自身状态

在等待期间，任务状态保持 `IN_PROGRESS`，日志记录等待状态。

## Step 6: 重测

所有 FIX 消息完成（DONE）后:

1. 重新部署/重新构建
2. 重新运行 E2E 测试
3. 如果仍有失败 → 新一轮 FIX 消息（最多 3 轮）
4. 第 3 轮仍失败 → 标记 FAILED

```
记录: "[E2E-RETRY] 第 {N}/3 轮重测: {pass}/{total} 通过"
```

## Step 7: 报告结果

### 全部通过

```bash
python3 skills/stage-bridge/scripts/write_artifact.py <req_id> "<task_name>" \
  e2e_report "$(cat test-report.json)"

python3 skills/stage-bridge/scripts/complete_task.py <req_id> <task_name>
```

### 重试耗尽

```bash
python3 skills/stage-bridge/scripts/fail_task.py <req_id> <task_name> \
  --error "E2E 测试 3 轮重试后仍未通过: {failed_tests}" \
  --retry-hint "manual"
```

## ABORT 检测

在以下节点检查 ABORT:
- 每轮重测前
- 发送 FIX 消息后
- 等待修复期间（每 30s）
