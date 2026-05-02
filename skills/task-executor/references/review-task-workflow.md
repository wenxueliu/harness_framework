# Review Task Workflow

代码/设计审查任务的执行流程。

## 流程

```
Step 1: 加载被审产物
Step 2: 确定审查维度
Step 3: 执行审查
Step 4: 输出审查报告
```

## Step 1: 加载被审产物

从 Consul context 读取被审查的产物（设计文档、代码 diff、PR URL 等）。

```bash
python3 skills/stage-bridge/scripts/read_context.py <req_id> pr_url
python3 skills/stage-bridge/scripts/read_context.py <req_id> design_doc
```

## Step 2: 确定审查维度

| 审查类型 | 关注点 |
|---------|--------|
| `logic` | 正确性、边界条件、错误处理、状态机 |
| `security` | 认证授权、注入、数据泄露、敏感信息 |
| `performance` | N+1 查询、缓存、连接池、索引 |
| `architecture` | 组件解耦、契约一致性、ADR 合规 |

## Step 3: 执行审查

按审查维度逐项检查。每发现一个问题记录:
- Level: P0 / P1 / P2 / P3
- Location: 文件路径 + 行号
- Description: 问题描述
- Fix: 修复建议

## Step 4: 输出审查报告

```bash
# 记录审查结果
python3 skills/stage-bridge/scripts/log_step.py <req_id> "<task_name>" \
  --type "REVIEW_REPORT" --message '{
    "reviewer": "security",
    "total_issues": 3,
    "p0": 0,
    "p1": 1,
    "p2": 2,
    "p3": 0,
    "summary": "发现 1 个认证绕过风险和 2 个输入校验不足"
  }'

# 写入详细报告到 context
python3 skills/stage-bridge/scripts/write_artifact.py <req_id> "<task_name>" \
  review_report_security "$(cat review-report.md)"

python3 skills/stage-bridge/scripts/complete_task.py <req_id> <task_name>
```
