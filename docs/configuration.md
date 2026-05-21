# 配置参考

## 启动参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--consul` | `$CONSUL_ADDR` 或 `127.0.0.1:8500` | Consul 地址 |
| `--token` | `$CONSUL_TOKEN` 或空 | Consul ACL Token |
| `--host` | `0.0.0.0` | WebAPI 监听地址 |
| `--port` | `8080` | WebAPI 端口 |
| `--aggregator-interval` | `5` | Aggregator 轮询间隔（秒） |
| `--watchdog-interval` | `30` | Watchdog 轮询间隔（秒） |
| `--task-timeout` | `120` | 单任务超时时间（秒） |
| `--heartbeat-timeout` | `120` | Agent 心跳超时（秒） |
| `--max-retry` | `3` | 任务最大重试次数 |
| `--log-level` | `INFO` | 日志级别：DEBUG/INFO/WARNING/ERROR |
| `--log-dir` | 空（仅 stdout） | 日志目录 |
| `--log-max-bytes` | `10485760` | 单个日志文件最大字节数 |
| `--log-backup-count` | `5` | 保留的旧日志文件数量 |

### 存储模式

| 参数 | 说明 |
|------|------|
| `--local` | 内存存储 + 内嵌 HTTP 服务器 |
| `--local-port` | 内嵌 HTTP 服务器端口（默认 8500） |
| `--local-data-file` | JSON 持久化文件路径（默认 `~/.harness/local_store.json`） |
| `--local-file` | 纯文件模式（自动启用单机模式） |
| `--standalone` | 单机模式：Agent 无需注册/心跳/注销 |
| `--standalone-agent-id` | 默认 Agent ID（默认 `standalone-agent`） |

### 组件开关

| 参数 | 说明 |
|------|------|
| `--no-aggregator` | 禁用 Aggregator |
| `--no-watchdog` | 禁用 Watchdog |
| `--no-webapi` | 禁用 WebAPI |

## 环境变量

| 变量 | 说明 |
|------|------|
| `CONSUL_ADDR` | Consul 地址（被 `--consul` 覆盖） |
| `CONSUL_TOKEN` | Consul ACL Token（被 `--token` 覆盖） |

**优先级**：命令行参数 > 环境变量

## 常用启动组合

```bash
# 最简启动（单机、零依赖、零心跳）
python -m harness_framework.daemon --local-file

# 开发调试（HTTP 通信、日志详细）
python -m harness_framework.daemon --local --log-level DEBUG

# 生产模式（Consul 后端）
python -m harness_framework.daemon --consul consul-cluster:8500 --token $CONSUL_TOKEN

# 仅跑调度，不要心跳检测（单机全自动）
python -m harness_framework.daemon --local-file --no-watchdog

# 高超时任务
python -m harness_framework.daemon --task-timeout 3600 --max-retry 5
```
