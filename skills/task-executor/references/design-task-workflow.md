# Design Task Workflow

设计任务的执行流程。

## 流程

```
Step 1: 加载需求上下文
Step 2: 编写设计文档
Step 3: 设计审查
Step 4: 修订
Step 5: 写入产物
```

## Step 1: 加载需求上下文

从 Consul 读取:
- 需求元数据: `workflows/<req_id>/title`
- 上游 context: `workflows/<req_id>/context/`（requirement_spec 等）

## Step 2: 编写设计文档

按设计模板编写:
- API 契约设计（OpenAPI 3.0）
- 数据模型设计
- 架构决策记录（ADR）
- 技术选型决策

## Step 3: 设计审查

启动 `hw-reviewer-logic` subagent 审查设计文档。

## Step 4: 修订

根据审查反馈修订设计文档。

## Step 5: 写入产物

```bash
# 写入设计文档到 context（下游 backend 任务可读）
python3 skills/stage-bridge/scripts/write_artifact.py <req_id> "<task_name>" \
  api_spec "$(cat api-spec.yaml)"

python3 skills/stage-bridge/scripts/write_artifact.py <req_id> "<task_name>" \
  design_doc_url "https://..."

python3 skills/stage-bridge/scripts/complete_task.py <req_id> <task_name>
```
