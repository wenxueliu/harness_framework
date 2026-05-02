# Deploy Task Workflow

部署任务的执行流程。

## 流程

```
Step 1: 确认部署条件
Step 2: 构建
Step 3: 部署
Step 4: 冒烟测试
Step 5: 报告结果
```

## Step 1: 确认部署条件

- 所有上游任务 DONE
- 部署环境可用
- Feature flag 已配置（如需要）

## Step 2: 构建

```bash
# 拉取最新代码
git checkout main
git pull

# 构建
docker build -t "$SERVICE_NAME:latest" .
```

## Step 3: 部署

```bash
# 部署到目标环境
docker push "$REGISTRY/$SERVICE_NAME:latest"
kubectl set image "deployment/$SERVICE_NAME" \
  "$SERVICE_NAME=$REGISTRY/$SERVICE_NAME:latest"

# 等待 rollout 完成
kubectl rollout status "deployment/$SERVICE_NAME" --timeout=5m
```

## Step 4: 冒烟测试

```bash
# 健康检查
curl -f "https://$SERVICE_URL/health"

# 关键 API 冒烟
curl -f "https://$SERVICE_URL/api/v1/..."
```

## Step 5: 报告结果

```bash
python3 skills/stage-bridge/scripts/write_artifact.py <req_id> "<task_name>" \
  deploy_version "$(git rev-parse HEAD)"

python3 skills/stage-bridge/scripts/complete_task.py <req_id> <task_name>
```
