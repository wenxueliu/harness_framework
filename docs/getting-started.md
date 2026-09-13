# 5 分钟入门：定义你的第一个工作流

> **初次接触？** 先看 [quickstart.md](quickstart.md)。本文带你定义第一个有依赖关系的 DAG 工作流。

这篇文章带你定义一个 3 个任务、有依赖关系的 DAG 流水线，然后看框架如何自动推进。

## 1. 创建工作流描述文件

新建 `my-first-dag.json`：

```json
{
  "design": {
    "type": "design",
    "depends_on": [],
    "acp": {"agent": "claude"},
    "description": "设计 REST API 端点：GET /api/tasks、POST /api/tasks"
  },
  "backend": {
    "type": "backend",
    "depends_on": ["design"],
    "acp": {"agent": "codex"},
    "description": "实现 /api/tasks 端点的 CRUD 逻辑"
  },
  "test": {
    "type": "test",
    "depends_on": ["backend"],
    "acp": {"agent": "codex"},
    "description": "对 /api/tasks 做集成测试：CRUD 四条路径 + 错误场景"
  }
}
```

**依赖关系**：
```
design ──→ backend ──→ test
```

- `design` 无依赖，是最上游的叶子任务
- `backend` 依赖 `design` 完成
- `test` 依赖 `backend` 完成

## 2. 启动框架

```bash
python -m harness_framework.daemon --local \
  --acp-workspace-root /absolute/path/to/your/project
```

保持终端开着。

`--acp-workspace-root` 是未在任务中设置 `acp.cwd` 时的默认 Agent 工作目录。
生产任务应明确指向实际项目目录；`service_name` 只是可选业务标签，不负责定位仓库。

### Workspace-first 执行

上面的命令保留了旧 CLI 的兼容路径。生产环境建议使用 Workspace-first 流程：

1. 用 `--workspace-root ALIAS=/absolute/path` 配置服务端允许的根目录；不要把任意绝对路径交给浏览器。
2. 在 Dashboard“全局配置”中登记 Project Workspace，并执行 Preflight。
3. 在项目组的工作流页面点击“启动 Run”，选择隔离策略和 Git ref。
4. Run 准备成功后，Dispatcher 才会认领任务；每个 Attempt 的 ACP cwd 都来自不可变 Binding。

`service_name` 不参与路由。`--demo-mode` 仅用于演示，允许显式 `DEMO_TEMP` 在 `/tmp` 创建临时目录；生产 managed Run 缺少 Workspace 会被拒绝。

## 3. 初始化工作流

```bash
python scripts/sync_to_consul.py my-first-dag.json \
  --req-id myapp-001 --title "我的第一个工作流" --publish
```

加了 `--publish`，工作流会被 Aggregator、ACPDispatcher 和 Watchdog 接管。

## 4. 启动 Dashboard

另开一个终端：

```bash
./scripts/start_dashboard.sh
```

脚本会检查 Node.js 版本，并在首次运行或依赖更新后自动安装依赖。保持这个终端开着。

浏览器访问 [http://127.0.0.1:3000/](http://127.0.0.1:3000/)。Dashboard 通过
`http://127.0.0.1:8080` 的 Harness WebAPI 读取真实任务数据；不要把 `8080/`
当作页面地址。

## 5. 观察 DAG 自动推进

```bash
# 查看整体状态
curl -s http://127.0.0.1:8080/api/workflows | python3 -m json.tool
```

你会看到：

```
myapp-001 → phase=RUNNING, progress=0%
├── design    → PENDING    ← Aggregator 检测到无依赖，已激活
├── backend   → BLOCKED    ← 等待 design 完成
└── test      → BLOCKED    ← 等待 backend 完成
```

**关键点**：`design` 被激活后，ACPDispatcher 会立即创建 Claude Agent，因此查询时可能已经是 `IN_PROGRESS` 或 `DONE`。`backend` 和 `test` 在上游完成前保持 `BLOCKED`。

## 6. 观察 Agent 自动执行

无需注册或启动常驻 Worker。等待几秒后再次查看：

```bash
curl -s http://127.0.0.1:8080/api/workflows | python3 -m json.tool
```

`design` 完成后 Aggregator 激活 `backend`，Dispatcher 随即创建 Codex Agent：

```
myapp-001 → phase=RUNNING, progress=33%
├── design    → DONE
├── backend   → IN_PROGRESS ← Codex ACP Agent 执行中
└── test      → BLOCKED
```

## 7. 等待工作流完成

同一链路会继续创建 backend 与 test 的 Codex Agent。再次查看，所有任务成功时流程结束：

```
myapp-001 → phase=DONE, progress=100%
```

## 理解：Agent 在实际中怎么工作

默认执行链路由框架自动管理：

1. **认领任务**：ACPDispatcher 对 `PENDING` 任务执行 CAS
2. **创建 Agent**：按 `acp.agent` 或任务类型启动 Claude/Codex adapter
3. **执行与完成**：通过 ACP session 发送任务，成功后标记 `DONE`
4. **框架推进**：Aggregator 检测到上游 DONE → 激活下游

Agent 不需要轮询整个 DAG；Dispatcher 只把当前步骤及显式上下文注入会话。

## 下一步

| 我想… | 看这里 |
|-------|--------|
| 理解任务状态机（BLOCKED → PENDING → IN_PROGRESS → DONE/FAILED） | [核心概念 →](concepts.md) |
| 配置 Claude/Codex adapter、权限和会话 | [ACP 执行架构 →](acp-execution.md) |
| 了解故障恢复（超时、Agent 死亡如何处理） | [核心概念 →](concepts.md) |
| 查看所有可用的 WebAPI 端点 | [操作手册 →](usage-guide.md) |
| 在任务执行中补充要求或人工修复 | [人工介入任务 →](human-task-interaction.md) |
