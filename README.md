# agent-platform 框架层

多 Agent 软件开发平台的**框架层**（Framework Layer）实现，本地 MVP 形态。

## 架构

框架层由单一 Python 进程承载，内部通过线程协作运行三大组件：

| 组件 | 职责 |
| :--- | :--- |
| **Aggregator** | 监听 DAG 状态变更，依赖满足时激活下游任务，所有反馈 FIXED 时触发重测 |
| **Watchdog** | 检测 IN_PROGRESS 任务的 Agent 是否存活，超时或宕机时自动恢复 |
| **WebAPI** | 为业务看板提供聚合查询与控制信号写入接口（含 CORS） |

执行层（Agent 进程）通过 `stage-bridge` Skill 与框架交互，所有协作状态存储在 **Consul KV** 中。

## 快速开始

### 1. 安装依赖

```bash
# 安装 Consul（macOS）
brew install consul

# 或 Linux
curl -LO https://releases.hashicorp.com/consul/1.18.1/consul_1.18.1_linux_amd64.zip
unzip consul_*.zip && sudo mv consul /usr/local/bin/

# 框架本身零外部 Python 依赖（仅标准库）
```

### 2. 启动 Consul dev mode

```bash
./scripts/start_consul_dev.sh
```

访问 [http://127.0.0.1:8500/ui](http://127.0.0.1:8500/ui) 查看 Consul 自带 UI。

### 3. 启动框架主进程

```bash
python -m agent_platform.daemon
```

默认在 `8080` 端口提供 WebAPI，可通过 `--port` 修改。

### 4. 初始化一个需求

```bash
python scripts/sync_to_consul.py req-001 examples/dependencies.example.json \
  --title "用户登录功能"
```

### 5. 手动启动 Agent

每个 Agent 由用户手动启动（参考 `stage-bridge` Skill 的 `register_agent.py` 命令）。Agent 通过 Consul service register 加入平台后，框架自动感知并将其纳入调度。

### 6. 启动业务看板

看板项目位于 `agent-dashboard/`，已配置直连 Consul。详见看板 README。

## 配置项

| 参数 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `--consul` | `127.0.0.1:8500` | Consul HTTP 地址 |
| `--token` | （空） | Consul ACL Token（dev mode 可省） |
| `--port` | `8080` | WebAPI 监听端口 |
| `--aggregator-interval` | `5` | DAG 推进轮询秒数 |
| `--watchdog-interval` | `30` | 僵尸任务检测秒数 |
| `--task-timeout` | `3600` | 单任务超时（秒） |

也可通过环境变量配置：`CONSUL_ADDR`、`CONSUL_TOKEN`。

## API 端点

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/workflows` | 所有需求列表（看板首屏） |
| GET | `/api/workflow/<req_id>` | 单需求完整状态（任务、反馈、上下文） |
| GET | `/api/agents` | 所有注册 Agent 列表 |
| POST | `/api/workflow/<req_id>/control` | 控制信号 `{"action":"PAUSE\|RESUME\|ABORT\|RETRY","task_name":"..."}` |

## 与 Consul UI 的关系

Consul 自带的 UI 适合查看 KV 原始内容、Service 注册情况、健康检查状态，**面向运维**。
本框架的业务看板面向**研发与产品**，提供 DAG 拓扑可视化、任务进度、人工干预等业务视角能力。两者互补使用。

## 后续扩展

未来从 dev mode 升级到生产 3 节点 Consul 集群时，业务代码无需改动，仅需修改 `--consul` 指向集群地址，并配置 ACL Token。
