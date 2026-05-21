# 存储模式详解

> **快速导航**：想快速上手？先看 [quickstart.md](quickstart.md) 和 [getting-started.md](getting-started.md)。想了解 Agent 如何接入？看 [agent-guide.md](agent-guide.md)。本文是三种存储后端的深度对比。

## 目录

- [一、架构总览](#一架构总览)
- [二、三种存储模式详解](#二三种存储模式详解)
  - [Consul 模式](#consul-模式)
  - [Local 模式（--local）](#local-模式---local)
  - [Local-File 模式（--local-file）](#local-file-模式---local-file)
  - [三模式对比](#三模式对比)
- [三、端到端执行流程](#三端到端执行流程)
  - [1. 需求定义阶段](#1-需求定义阶段)
  - [2. 启动框架与初始化需求](#2-启动框架与初始化需求)
  - [3. Agent 注册与心跳](#3-agent-注册与心跳)
  - [4. 任务推进（Aggregator）](#4-任务推进aggregator)
  - [5. Agent 抢占与执行任务](#5-agent-抢占与执行任务)
  - [6. 故障恢复（Watchdog）](#6-故障恢复watchdog)
  - [7. 测试失败反馈闭环](#7-测试失败反馈闭环)
  - [8. 动态任务提案](#8-动态任务提案)
  - [9. 流程终止](#9-流程终止)
- [四、Agent 适配指南：一个代码仓如何接入框架](#四agent-适配指南一个代码仓如何接入框架)
- [五、三种模式在端到端流程中的差异](#五三种模式在端到端流程中的差异)
- [六、FAQ](#六faq)

---

## 一、架构总览

Harness Framework 采用**分层架构**，核心原则是**框架与存储解耦**、**Agent 与框架通过共享状态协作**。

```
┌─────────────────────────────────────────────────────────────┐
│                      框架主进程 (daemon.py)                    │
│  ┌────────────┐  ┌──────────┐  ┌──────────┐                │
│  │ Aggregator │  │ Watchdog │  │ WebAPI   │                │
│  │ DAG 推进   │  │ 故障恢复  │  │ HTTP API │                │
│  └─────┬──────┘  └────┬─────┘  └────┬─────┘                │
│        │              │             │                       │
│        └──────────────┼─────────────┘                       │
│                       │                                     │
│              ┌────────▼────────┐                            │
│              │   KVStore 接口   │  ← kv_store_protocol.py    │
│              └────────┬────────┘                            │
└───────────────────────┼─────────────────────────────────────┘
                        │
          ┌─────────────┼─────────────┐
          ▼              ▼             ▼
   ┌──────────┐  ┌────────────┐  ┌──────────┐
   │ Consul   │  │ LocalStore │  │ FileStore│
   │ 客户端   │  │ 内存+HTTP  │  │ 文件存储  │
   └──────────┘  └────────────┘  └──────────┘
```

**关键设计**：

1. **KVStore Protocol**（`kv_store_protocol.py`）定义了统一的存储接口：`kv_get`、`kv_put`、`kv_delete`、`kv_blocking_get`、`list_services`。三个存储后端各自实现该接口。
2. **框架三大组件（Aggregator、Watchdog、WebAPI）只依赖于 KVStore 接口**，不感知底层是 Consul、内存还是文件。
3. **Agent 通过存储层的通信机制与框架协作**，不向框架进程发起任何 RPC——Agent 读写共享状态，框架轮询共享状态，彼此完全异步。

这种解耦使得切换存储后端只需一个命令行参数，代码零改动。

---

## 二、三种存储模式详解

### Consul 模式

#### 架构

```
┌─────────┐    HTTP     ┌──────────┐    HTTP     ┌──────────────┐
│  daemon ├────────────►│  Consul  │◄────────────┤ Agent 进程   │
│ (框架)   │ 8500       │  KV +    │   8500      │ (通过 curl / │
│          │            │  Health  │             │  stage-bridge)│
└─────────┘            └──────────┘             └──────────────┘
```

这是**默认模式**。框架和 Agent 都作为 HTTP 客户端连接到 Consul 服务，通过 Consul KV 读写共享状态，通过 Consul Health 跟踪 Agent 存活。

#### 启动方式

```bash
# 终端 1：启动 Consul（开发模式）
./scripts/start_consul_dev.sh
# 或 consul agent -dev -ui -bind=127.0.0.1

# 终端 2：启动框架（默认连接 127.0.0.1:8500）
python -m harness_framework.daemon

# 或指定独立的 Consul 地址
python -m harness_framework.daemon --consul 10.0.0.10:8500

# 如需 ACL Token
python -m harness_framework.daemon --consul 10.0.0.10:8500 --token "your-token"
```

#### 核心特性

| 特性 | 实现方式 |
|------|----------|
| **数据存储** | Consul 服务端内存 + Raft 日志持久化到磁盘 |
| **并发安全** | Consul 原生 CAS（Check-And-Set），通过 `?cas=<ModifyIndex>` 参数实现 |
| **Agent 心跳** | Consul TTL Check——Agent 每 10s 调用 `PUT /v1/agent/check/pass/service:<agent_id>`，30s 未收到标记为 `critical`，2 分钟后自动注销 |
| **Blocking Query** | Consul 原生支持 `?index=<X-Consul-Index>&wait=30s` 长轮询，实现状态变更的秒级推送 |
| **持久化** | Raft 多数派写入，节点故障不丢数据 |
| **可观测性** | Consul 自带 Web UI（`http://127.0.0.1:8500/ui`）查看原始 KV |
| **服务发现** | Consul Service Catalog——Agent 通过 `PUT /v1/agent/service/register` 注册，Watchdog 查询存活 |

#### Agent 通信方式

Agent 通过 HTTP 直接与 Consul 通信：

```bash
# 读 KV（获取任务状态）
curl -s "http://$CONSUL_ADDR/v1/kv/workflows/req-001/tasks/design/status?raw"

# CAS 写 KV（抢占任务）
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/req-001/tasks/design/status?cas=$INDEX" -d "IN_PROGRESS"

# 注册服务（心跳用）
curl -s -X PUT "http://$CONSUL_ADDR/v1/agent/service/register" \
  -H "Content-Type: application/json" \
  -d '{"ID":"agent-1","Name":"agent-worker","Check":{"CheckID":"service:agent-1","TTL":"30s"}}'

# 心跳
curl -s -X PUT "http://$CONSUL_ADDR/v1/agent/check/pass/service:agent-1"
```

#### 启动 Consul 的注意事项

**方式一：开发模式（推荐用于本地开发）**

```bash
consul agent -dev -ui -bind=127.0.0.1
```

- `-dev`：单节点，所有数据在内存，重启即丢失
- `-ui`：启用 Consul 自带 Web UI
- `-bind=127.0.0.1`：仅监听本地，安全

**方式二：服务器模式（用于持久化测试）**

```bash
mkdir -p consul_data
consul agent -server -ui -bootstrap-expect=1 \
  --node harness_framework \
  -data-dir="consul_data" \
  -bind="127.0.0.1" -client="0.0.0.0"
```

- 数据写入 `consul_data/` 目录，重启不丢
- `-client="0.0.0.0"`：允许其他机器访问（注意安全）

**启动脚本**：项目提供了一键脚本 `scripts/start_consul_dev.sh`，自动以开发模式启动。

**典型启动日志**：

```
==> Starting Consul agent...
           Version: '1.18.1'
       Node name: 'harness-framework'
      Datacenter: 'dc1' (Segment: '<all>')
          Server: true
   Bootstrap Mode: true
==> Consul agent running!
           HTTP: http://127.0.0.1:8500
            RPC: 127.0.0.1:8300
```

#### 适用场景

- **生产环境**：多节点 Consul 集群保证高可用
- **多人协作**：多个开发者共享同一个 Consul 集群
- **涉及复杂故障恢复**：需要 Consul 的 TTL Check 和自动注销能力
- **需要持久化状态**：Consul Raft 保证数据不丢

---

### Local 模式（`--local`）

#### 架构

```
┌────────────────────────────────────────────────┐
│                  daemon 进程                      │
│  ┌──────────┐  ┌──────────────────────────┐    │
│  │ Aggregator│  │    LocalStore            │    │
│  │ Watchdog  │  │  ┌──────────────────┐  │    │
│  │ WebAPI    │  │  │   线程安全内存     │  │    │
│  └─────┬─────┘  │  │    KV 存储        │  │    │
│        │         │  │  (RLock 保护)     │  │    │
│        │         │  └────────┬─────────┘  │    │
│        │         │           │            │    │
│        │         │  ┌────────▼─────────┐  │    │
│        └─────────┤  │  LocalConsulHandler │  │    │
│                  │  │  内嵌 HTTP 服务器    │  │    │
│                  │  │  (ThreadingHTTPServer)│  │    │
│                  │  └────────┬─────────┘  │    │
│                  └───────────┼─────────────┘    │
└──────────────────────────────┼──────────────────┘
                               │ HTTP (默认 8500)
                               ▼
                        ┌──────────────┐
                        │ Agent 进程    │
                        │ (通过 curl /  │
                        │  stage-bridge)│
                        └──────────────┘
```

Local 模式是 Consul 模式的**零依赖替代**。它在框架进程内部启动一个 `ThreadingHTTPServer`，实现了 Consul v1 API 的一个子集（KV 读写、服务注册/注销、心跳、Health 查询），让 Agent 在使用完全相同的 HTTP 协议栈时无需安装 Consul。

#### 核心实现

**LocalStore**（`local_store.py`）：

- 所有数据存储在 Python 进程内存的 `dict[str, tuple[str, int]]` 中
- 使用 `threading.RLock` 保证多线程安全
- 每个 key 关联一个 `ModifyIndex`（单调递增），支持 CAS 写入
- 可选的 JSON 文件持久化（周期性自动保存 + 脏标记机制）

```python
# 核心数据结构
self._store: dict[str, tuple[str, int]] = {}  # key -> (value, modify_index)
self._global_index: int = 100                  # 单调递增的全局索引
self._heartbeats: dict[str, float] = {}        # agent_id -> 最后心跳时间戳
self._agent_services: dict[str, dict] = {}     # agent_id -> 注册信息
```

**LocalConsulHandler**（`local_store.py`）：

实现了 Consul v1 API 的以下端点：

| 端点 | 方法 | 功能 |
|------|------|------|
| `/v1/kv/<key>` | GET | KV 读取（支持 `?recurse`、`?wait`、`?index`） |
| `/v1/kv/<key>` | PUT | KV 写入（支持 `?cas=<index>`） |
| `/v1/kv/<key>` | DELETE | KV 删除（支持 `?recurse`） |
| `/v1/health/service/<name>` | GET | 健康服务查询 |
| `/v1/agent/service/register` | PUT | Agent 注册 |
| `/v1/agent/service/deregister/<id>` | PUT | Agent 注销 |
| `/v1/agent/check/pass/<id>` | PUT | Agent 心跳 |
| `/v1/status/leader` | GET | 伪装 leader 信息 |
| `/v1/agent/self` | GET | 节点自身信息 |

#### 启动方式

```bash
# 基本启动（内存存储 + HTTP 8500）
python -m harness_framework.daemon --local

# 指定端口和数据文件
python -m harness_framework.daemon --local \
  --local-port 9500 \
  --local-data-file /tmp/harness_store.json

# 不带持久化（纯内存，重启丢失）
python -m harness_framework.daemon --local
```

#### 持久化机制

`--local-data-file` 指定 JSON 文件的路径，默认为 `~/.harness/local_store.json`：

1. **自动加载**：启动时若文件存在，自动加载到内存
2. **自动保存**：后台线程每 5 秒检查脏标记（`_dirty` flag），有变更则写入
3. **原子写入**：先写 `.tmp` 文件，再 `os.replace` 覆盖原文件，保证写不损坏
4. **关闭保存**：进程退出时调用 `flush()` 强制保存

```json
// ~/.harness/local_store.json 示例
{
  "global_index": 142,
  "store": {
    "workflows/req-001/title": ["用户登录功能", 101],
    "workflows/req-001/tasks/design/status": ["DONE", 105],
    "workflows/req-001/tasks/design/type": ["design", 102],
    "workflows/req-001/tasks/backend/status": ["IN_PROGRESS", 110]
  },
  "heartbeats": {
    "agent-user-service-01": 1712345678.123
  },
  "agent_services": {
    "agent-user-service-01": {
      "ID": "agent-user-service-01",
      "Name": "agent-worker",
      "Tags": ["capability=backend"],
      "Meta": {"service_name": "user-service"}
    }
  }
}
```

#### Agent 通信方式

与 Consul 模式**完全一致**，Agent 仍然通过 HTTP 与 `CONSUL_ADDR` 通信：

```bash
# Agent 设置
export CONSUL_ADDR=127.0.0.1:8500  # 指向框架内嵌的 HTTP 服务器

# 后续所有 curl / stage-bridge 脚本用法与 Consul 模式完全相同
curl -s "http://$CONSUL_ADDR/v1/kv/workflows/req-001/tasks/design/status?raw"
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/req-001/tasks/design/status?cas=$INDEX" -d "IN_PROGRESS"
```

#### 适用场景

- **本地开发测试**：开发者不需要安装 Consul 即可运行完整系统
- **涉及 Agent 的调试**：Agent 通过标准 HTTP 协议连接，行为与生产环境一致
- **持续集成**：CI 环境无需安装 Consul 即可跑集成测试
- **演示和教学**：快速启动无需外部依赖

---

### Local-File 模式（`--local-file`）

#### 架构

```
┌────────────────────────────────────────┐
│              daemon 进程                 │
│  ┌──────────┐                          │
│  │ Aggregator│   ┌──────────────┐       │
│  │ Watchdog  │   │   FileStore  │       │
│  │ WebAPI    │   │  JSON 文件   │       │
│  └─────┬─────┘   │  + flock     │       │
│        │         └──────┬───────┘       │
│        └────────────────┘               │
└─────────────────────────────────────────┘
                     │
                     │ 同一 JSON 文件
                     │ (fcntl.flock 保护)
                     ▼
┌─────────────────────────────────────────┐
│  Agent 进程                               │
│  file_kv.py CLI → 读写同一 JSON 文件      │
│  无 HTTP，无网络                           │
└─────────────────────────────────────────┘
```

Local-File 模式是**零依赖、零网络**的极致简化版本。所有状态存储在单个 JSON 文件中，框架和 Agent 都通过 `fcntl.flock` 加锁后直接读写该文件。

#### 核心实现

**FileStore**（`file_store.py`）：

- 数据存储在 JSON 文件中，文件路径默认为 `~/.harness/file_store.json`
- 使用 `fcntl.flock` 实现进程间互斥（跨进程安全）
- 每个操作都是 "acquire lock → read → modify → write → release lock" 的原子序列
- 支持 CAS 写入（读当前 ModifyIndex → CAS 比对 → 写入）
- 使用 `.lock` 文件作为锁文件，`.tmp` 文件实现原子写入

```python
def _atomic_read_modify_write(self, modifier) -> Any:
    """获取锁 → 读取 → 修改 → 写入 → 释放锁。"""
    fd = self._acquire_lock()       # fcntl.flock(fd, LOCK_EX)
    try:
        data = self._read_data()    # json.load
        result = modifier(data)
        self._write_data(data)      # tmp + os.replace
        return result
    finally:
        self._release_lock(fd)      # fcntl.flock(fd, LOCK_UN)
```

**并发安全机制**：

| 层面 | 机制 | 说明 |
|------|------|------|
| 进程间互斥 | `fcntl.flock` 排他锁 | 同一时刻只有一个进程能读写文件 |
| 写入原子性 | `tmp` + `os.replace` | 写过程中 crash 不会损坏原文件 |
| 线程安全 | `flock` 本身是进程级的，同一进程多线程共享 fd | 框架内部各组件使用同一个 FileStore 实例 |
| CAS 原子性 | 在锁保护下读 ModifyIndex → 比对 → 写入 | 保证抢占和条件更新不丢失 |

#### 启动方式

```bash
# 基本启动
python -m harness_framework.daemon --local-file

# 指定数据文件路径
python -m harness_framework.daemon --local-file --local-data-file /tmp/my_store.json
```

#### Agent 通信方式

Agent **不能**通过 HTTP 或 curl 操作。必须使用 `scripts/file_kv.py` CLI：

```bash
# 1. 设置数据文件路径
DATA_FILE=~/.harness/file_store.json

# 2. KV 读写
python scripts/file_kv.py --data-file "$DATA_FILE" put workflows/req-001/tasks/design/status PENDING
python scripts/file_kv.py --data-file "$DATA_FILE" get workflows/req-001/tasks/design/status
python scripts/file_kv.py --data-file "$DATA_FILE" get workflows/ --recurse
python scripts/file_kv.py --data-file "$DATA_FILE" delete workflows/req-001/tasks/design

# 3. CAS 写入（抢占任务）
python scripts/file_kv.py --data-file "$DATA_FILE" put workflows/req-001/tasks/design/status IN_PROGRESS --cas 105

# 4. Blocking Get（阻塞等待变更）
python scripts/file_kv.py --data-file "$DATA_FILE" blocking-get workflows/req-001/tasks/backend/status --index 110 --wait 30s

# 5. Agent 注册与心跳
python scripts/file_kv.py --data-file "$DATA_FILE" register '{"ID":"agent-1","Name":"agent-worker","Tags":["capability=backend"],"Meta":{}}'
python scripts/file_kv.py --data-file "$DATA_FILE" heartbeat agent-1
python scripts/file_kv.py --data-file "$DATA_FILE" deregister agent-1
python scripts/file_kv.py --data-file "$DATA_FILE" list-services

# 6. 状态 leader（兼容性端点）
python scripts/file_kv.py --data-file "$DATA_FILE" status-leader
```

`file_kv.py` CLI 的输出格式与 Consul API 兼容，使得 Agent 的适配成本最低：

```bash
# 返回格式与 Consul API 一致
$ python scripts/file_kv.py --data-file /tmp/store.json get workflows/req-001/tasks/design/status
[{"Key": "workflows/req-001/tasks/design/status", "Value": "RE5PRQ==", "ModifyIndex": 105}]
```

#### 适用场景

- **单一开发者本地调试**：最简单、最轻量的启动方式
- **快速验证框架行为**：不需要启动任何额外进程
- **在没有网络的环境中使用**：零网络依赖
- **演示基础功能**：无需任何外部工具，一个命令就能跑起来
- **编写自动化测试**：测试 Fixture 可以直接操作 JSON 文件准备数据

#### 局限性

- 节点间并发 `fcntl.flock` 只对同一台机器的进程有效，不支持多机器共享
- 文件级别的排他锁，大规模并发时性能受限
- 与 Consul 模式相比，缺少高级特性（多数据中心、ACL、健康检查的多样策略）

---

### 三模式对比

| 对比维度 | Consul | `--local` | `--local-file` |
|----------|--------|-----------|-----------------|
| **外部依赖** | Consul 二进制（需安装） | 无 | 无 |
| **进程拓扑** | 3 个独立进程：框架 + Consul + Agent(s) | 2 个进程：框架（含内嵌 HTTP）+ Agent(s) | 1 个 JSON 文件 + 任意进程 |
| **Agent 通信方式** | HTTP → Consul（8500） | HTTP → 框架内嵌服务器（8500） | `file_kv.py` CLI → JSON 文件 |
| **并发安全** | Consul CAS（服务端乐观锁） | `threading.RLock`（进程内线程锁） | `fcntl.flock`（进程间文件锁） |
| **持久化** | Consul Raft 日志 | 可选 JSON 持久化（5s 自动保存） | JSON 文件（每次写入即时持久化） |
| **Blocking Query** | 原生支持（长轮询） | 模拟支持（0.5s 轮询） | 模拟支持（0.5s 轮询） |
| **Agent 心跳检测** | Consul TTL Check（30s critical, 2m 注销） | 框架进程内心跳表 + 超时判定 | 文件内心跳时间戳 + 超时判定 |
| **CAS 写入** | 支持 (`?cas=`) | 支持 (`?cas=`) | 支持 (`--cas`) |
| **数据可观测性** | Consul UI（原生） | WebAPI（框架自带） | 直接查看 JSON 文件 |
| **重启保留数据** | 是（Raft） | 是（指定 `--local-data-file` 时） | 是（始终持久化） |
| **多机器协作** | 支持 | 不支持（单机） | 不支持（单机） |
| **启动时间** | 需先启动 Consul（~3s） | 即时 | 即时 |
| **适用场景** | 生产、多人协作、持久化 | 开发测试含 Agent、演示 | 单机调试、快速验证、自动化测试 |
| **链路复杂 Agent 脚本** | 直接使用 curl | 直接使用 curl | 使用 `file_kv.py` CLI |

**升级路径**：Local → Local-File → Consul 是渐进式增强。从 local-file 升级到 local（改个参数），再升级到 Consul（启动 Consul 后去掉 `--local` 参数），全程不需要修改 Agent 代码。

---

## 三、端到端执行流程

下面以一个完整的需求"用户登录功能"为例，展示从需求定义到交付的全链路协作过程。用户提供需求，框架编排任务、管理状态、保证韧性，Agent 执行实际开发工作。

### 1. 需求定义阶段

**参与者**：人或设计 Agent

编写 `dependencies.json`，定义任务拓扑：

```json
{
  "design-api": {
    "type": "design",
    "depends_on": [],
    "service_name": "platform",
    "description": "设计登录 API 契约：register / login / logout 端点"
  },
  "review-design": {
    "type": "review",
    "depends_on": ["design-api"],
    "service_name": "platform",
    "description": "评审 API 设计"
  },
  "build-backend": {
    "type": "backend",
    "depends_on": ["review-design"],
    "service_name": "user-service",
    "description": "实现用户登录接口"
  },
  "test-e2e": {
    "type": "test",
    "depends_on": ["build-backend"],
    "service_name": "platform",
    "description": "端到端测试：正常登录、密码错误、账号不存在"
  }
}
```

每个任务包含：
- `type`：任务类型（design / review / backend / test / deploy）
- `depends_on`：依赖的上游任务列表（空数组表示叶子任务，无依赖）
- `service_name`：归属服务，Agent 通过这个字段过滤自己能认领的任务
- `description`：任务描述，Agent 读取后自主执行

DAG 拓扑：

```
design-api ──→ review-design ──→ build-backend ──→ test-e2e
(PENDING)       (BLOCKED)         (BLOCKED)         (BLOCKED)
```

### 2. 启动框架与初始化需求

#### 第一步：启动框架

**Consul 模式**：
```bash
# 终端 1：启动 Consul
./scripts/start_consul_dev.sh

# 终端 2：启动框架
python -m harness_framework.daemon

# 输出
# [daemon] INFO  Consul 连接成功: 127.0.0.1:8500
# [daemon] INFO  harness-framework daemon 已启动，按 Ctrl+C 退出
# [aggregator] INFO  Aggregator 已启动 (interval=5s)
# [watchdog] INFO  Watchdog 已启动 (interval=30s)
# [webapi] INFO  WebAPI 已启动 on 0.0.0.0:8080
```

**Local 模式**（无需 Consul）：
```bash
python -m harness_framework.daemon --local

# 输出
# [local_store] INFO  LocalStore loaded from ~/.harness/local_store.json (0 keys)
# [local_store] INFO  Local Consul HTTP server listening on 0.0.0.0:8500
# [daemon] INFO  LocalStore 已启动 (HTTP on 0.0.0.0:8500, ...)
```

**Local-File 模式**（最简，无需 HTTP）：
```bash
python -m harness_framework.daemon --local-file

# 输出
# [daemon] INFO  FileStore 已启动 (data=..., 无 HTTP 服务器)
# [daemon] INFO  Agent 请使用 scripts/file_kv.py --data-file '...' 操作 KV
```

#### 第二步：同步需求到存储

框架启动后，执行 `sync_to_consul.py` 将 `dependencies.json` 转换为 Consul KV 结构：

```bash
python scripts/sync_to_consul.py req-001 examples/dependencies.example.json \
  --title "用户登录功能" --publish
```

`--publish` 参数将 `workflows/req-001/published` 设为 `true`，告诉 Aggregator 和 Watchdog 开始处理这个需求。

此脚本在存储中创建以下 KV 结构：

```
workflows/req-001/
├── published: "true"
├── title: "用户登录功能"
├── priority: "0"
├── dependencies: <JSON 字符串>
├── created_at: <ISO 时间戳>
├── tasks/
│   ├── design-api/
│   │   ├── status: "PENDING"       ← 叶子任务，直接 PENDING
│   │   ├── type: "design"
│   │   ├── service_name: "platform"
│   │   └── description: "设计登录 API 契约..."
│   ├── review-design/
│   │   ├── status: "BLOCKED"       ← 有依赖，初始 BLOCKED
│   │   ├── type: "review"
│   │   ├── service_name: "platform"
│   │   └── description: "评审 API 设计"
│   ├── build-backend/
│   │   ├── status: "BLOCKED"
│   │   ├── type: "backend"
│   │   ├── service_name: "user-service"
│   │   └── description: "实现用户登录接口"
│   └── test-e2e/
│       ├── status: "BLOCKED"
│       ├── type: "test"
│       └── service_name: "platform"
```

#### 第三步：框架自动激活

Aggregator 在第一次轮询时发现 `design-api` 是叶子任务（无依赖依赖且已 `PENDING`），无需额外激活。非叶子任务保持 `BLOCKED`。

**此刻系统状态**：
- 框架运行中：Aggregator 每 5s 轮询，Watchdog 每 30s 轮询
- 需求已发布，design-api 已就绪
- WebAPI 在 8080 端口可查询 `http://127.0.0.1:8080/api/workflows`
- 等待 Agent 来认领 `design-api` 任务

### 3. Agent 注册与心跳

#### Agent 注册

每种存储模式下的 Agent 注册方式不同，但注册的信息一致：

**Consul / Local 模式**（HTTP）：
```bash
export CONSUL_ADDR=127.0.0.1:8500
export AGENT_ID=design-agent-01
export SERVICE_NAME=platform

# 注册到 Consul Service Catalog（或 LocalStore 的心跳表）
curl -s -X PUT "http://$CONSUL_ADDR/v1/agent/service/register" \
  -H "Content-Type: application/json" \
  -d "{
    \"ID\": \"$AGENT_ID\",
    \"Name\": \"agent-worker\",
    \"Tags\": [\"capability=design\", \"env=local\"],
    \"Meta\": {
      \"service_name\": \"$SERVICE_NAME\"
    },
    \"Check\": {
      \"CheckID\": \"service:$AGENT_ID\",
      \"TTL\": \"30s\"
    }
  }"
```

**Local-File 模式**（CLI）：
```bash
python scripts/file_kv.py --data-file ~/.harness/file_store.json register \
  '{"ID":"design-agent-01","Name":"agent-worker","Tags":["capability=design"],"Meta":{"service_name":"platform"}}'
```

#### 心跳维持

Agent 注册后必须持续发送心跳，否则 Watchdog 会判定为死亡并回滚其任务。

**Consul / Local 模式**（建议后台脚本）：
```bash
# 使用 stage-bridge 的心跳脚本，每 10 秒发送一次
AGENT_ID=design-agent-01 python3 skills/stage-bridge/scripts/heartbeat.py --loop 10 &

# 或手动 curl（后台）
while sleep 10; do
  curl -s -X PUT "http://$CONSUL_ADDR/v1/agent/check/pass/service:design-agent-01"
done &
```

**Local-File 模式**：
```bash
while sleep 10; do
  python scripts/file_kv.py --data-file ~/.harness/file_store.json heartbeat design-agent-01
done &
```

#### Agent 生命周期状态

```
┌──────────┐  register   ┌──────────────────┐  30s 无心跳   ┌──────────┐
│ 未注册    │──────────►│  存活 (passing)   │─────────────►│ critical │
└──────────┘            └──────────────────┘              └──────────┘
                               │ 2分钟无心跳                  │ 恢复心跳
                               │ 自动注销                     │
                               ▼                             ▼
                        ┌──────────────┐              ┌──────────┐
                        │ 已注销        │              │ 存活      │
                        └──────────────┘              └──────────┘
```

### 4. 任务推进（Aggregator）

Aggregator 是框架的 DAG 推进引擎，以固定间隔（默认 5s）轮询所有 `published=true` 的 workflow，执行以下逻辑：

```
每 5 秒：
  for each workflow where published == true:
    1. 检查 control 信号 → PAUSE/ABORT 则跳过
    2. 读取所有任务状态
    3. 对于每个 BLOCKED 任务，检查其 depends_on 是否全部 DONE
    4. 若是 → 将该任务状态改为 PENDING
```

**DAG 推进过程**：

```
时间 T=0 （初始化后）
  design-api:    PENDING  ← 叶子任务，框架未改动
  review-design:  BLOCKED  ← 等 design-api DONE
  build-backend:  BLOCKED  ← 等 review-design DONE
  test-e2e:       BLOCKED  ← 等 build-backend DONE

时间 T=Tx （design-api 被 Agent 完成，DONE）
  Aggregator 轮询检测到 design-api 为 DONE
  review-design 的依赖全部满足 → 激活为 PENDING
  └── design-api:  DONE
  └── review-design: PENDING  ← Aggregator 刚激活
  └── build-backend: BLOCKED
  └── test-e2e:      BLOCKED

时间 T=Ty （review-design DONE）
  Aggregator 激活 build-backend 为 PENDING
  └── build-backend: PENDING  ← 等待 user-service 的 Agent 认领

时间 T=Tz （build-backend DONE）
  Aggregator 激活 test-e2e 为 PENDING
```

**关键特性**：
- Aggregator **不做能力匹配**、**不分配 Agent**，它唯一的工作是 BLOCKED → PENDING
- 同一时间没有任务可推进时，轮询几乎零开销（只用一次 `kv_get` 遍历所有状态）
- Aggregator 不持久化任何状态，重启后立即恢复

### 5. Agent 抢占与执行任务

#### 5.1 抢占任务

Agent 使用 CAS（Compare-And-Swap）机制抢占 `PENDING` 任务。核心目标是：**多个 Agent 可能同时抢同一个任务，只有第一个能成功**。

**Consul / Local 模式**：

```bash
# 1. 查询任务状态，获取当前 ModifyIndex
RESP=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/req-001/tasks/design-api/status")
INDEX=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['ModifyIndex'])")
STATUS=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)[0].get('Value','') or '')" | base64 -d)

# 2. 检查确实是 PENDING
if [ "$STATUS" != "PENDING" ]; then
  echo "任务不可抢占，当前状态: $STATUS"
  exit 1
fi

# 3. CAS 写入 IN_PROGRESS（关键：带上 ModifyIndex）
#    如果另一个 Agent 抢先了，ModifyIndex 已变，本次写入返回 false
RESULT=$(curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/req-001/tasks/design-api/status?cas=$INDEX" -d "IN_PROGRESS")

# 4. 返回 "true" 则抢占成功
if [ "$RESULT" == "true" ]; then
  echo "抢占成功"
  # 写入抢占元数据
  curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/req-001/tasks/design-api/assigned_agent" -d "$AGENT_ID"
  curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/req-001/tasks/design-api/started_at" -d "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
else
  echo "抢占失败（CAS 冲突）"
fi
```

**Local-File 模式**：

```bash
# 使用 --cas 参数做原子抢占
python scripts/file_kv.py --data-file ~/.harness/file_store.json put \
  workflows/req-001/tasks/design-api/status IN_PROGRESS --cas 105

# 退出码 0 表示成功，1 表示 CAS 冲突
```

**CAS 竞争示意**：

```
Agent A (design-agent-01)              Agent B (design-agent-02)
        │                                      │
        │  GET status → PENDING (idx=100)      │
        │                                      │  GET status → PENDING (idx=100)
        │                                      │
        │  PUT status=IN_PROGRESS (cas=100)    │
        │  → true (成功)                       │
        │                                      │  PUT status=IN_PROGRESS (cas=100)
        │                                      │  → false (失败，ModifyIndex 已变为 101)
        │                                      │
  抢占成功!                                    抢占失败!
```

#### 5.2 执行任务

抢占成功后，Agent 执行以下步骤：

```
1. [必检] check-control ← 检查是否收到 ABORT 信号
2. read-context        ← 读取上游 Agent 产物
3. 实际开发工作        ← 设计/编码/测试/部署
4. [必检] check-control ← LLM 调用前后都要检查
5. write-artifact      ← 写入产物
6. log-step            ← 记录执行日志
7. complete-task       ← 标记 DONE
```

**具体操作**：

```bash
# 1. ABORT 检查（每次 LLM 调用前/后、关键步骤前）
CONTROL=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/req-001/control?raw")
if [ "$CONTROL" = "ABORT" ]; then
  echo "收到 ABORT 信号，退出"
  # 标记任务为 FAILED，原因 aborted
  curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/req-001/tasks/design-api/status" -d "FAILED"
  curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/req-001/tasks/design-api/error_message" -d "ABORTED"
  exit 7
fi

# 2. 读取上下文（上游产物）
curl -s "http://$CONSUL_ADDR/v1/kv/workflows/req-001/context/?recurse=true"

# 3. 执行实际开发工作（AI 生成代码、设计文档等）
#    ...

# 4. 写产物到上下文（全局共享）
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/req-001/context/api_spec" \
  -d '{"endpoints": [{"path": "/api/login", "method": "POST"}]}'

# 5. 记录步骤日志
SEQ=$(date +%s%3N)
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/req-001/sessions/design-api/$SESSION_ID/events/$SEQ" \
  -d '{"ts":"...","level":"info","message":"API 设计完成"}'

# 6. 完成任务（CAS 将 IN_PROGRESS → DONE）
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/req-001/tasks/design-api/status" -d "DONE"
```

#### 5.3 Aggregator 推进下游

Agent 将 `design-api` 标记为 `DONE` 后，Aggregator 在下一次轮询中检测到变更，将 `review-design` 从 `BLOCKED` 激活为 `PENDING`，然后等待 review Agent 来抢占。

```
用户可见进度（通过 WebAPI 查看）：
GET /api/workflows

Response:
{
  "req-001": {
    "title": "用户登录功能",
    "phase": "RUNNING",
    "progress": 25.0,           ← 1/4 任务完成
    "tasks": {
      "design-api": "DONE",
      "review-design": "PENDING",
      "build-backend": "BLOCKED",
      "test-e2e": "BLOCKED"
    }
  }
}
```

### 6. 故障恢复（Watchdog）

Watchdog 是框架的韧性保障组件，以固定间隔（默认 30s）扫描所有 `IN_PROGRESS` 任务，检测两类故障：

#### 故障类型一：Agent 死亡

Agent 进程崩溃或网络断开，心跳停止发送。

**检测逻辑**：

```python
# Watchdog 伪代码
for each task with status == IN_PROGRESS:
    agent_id = task.assigned_agent
    agents = consul.list_services("agent-worker")
    alive_ids = {a["Service"]["ID"] for a in agents}

    if agent_id not in alive_ids:
        # Agent 死亡！回滚任务
        if task.retry_count < max_retry:
            rollback_to_pending(task)  # 重置为 PENDING，retry_count++
        else:
            mark_failed(task)          # 超过最大重试次数，标记 FAILED
```

#### 故障类型二：任务超时

Agent 虽然存活（有心跳），但任务执行时间超过 `--task-timeout`（默认 120s）。

**检测逻辑**：

```python
for each task with status == IN_PROGRESS:
    elapsed = now - task.started_at
    if elapsed > task_timeout:
        # 任务超时！回滚
        if task.retry_count < max_retry:
            rollback_to_pending(task)
        else:
            mark_failed(task)
```

#### 故障恢复过程

```
正常执行:
  design-api: IN_PROGRESS (assigned_agent=design-agent-01)
                                      时间轴
                                        │
Agent 崩溃 ────────────────────────────┤
心跳停止                                │
                                        │
30s 后 Watchdog 扫描 ──────────────────┤
  → design-agent-01 不在存活列表
  → design-api: retry_count=0 < 3
  → 回滚为 PENDING
                                        │
新 Agent 抢占 ─────────────────────────┤
  → design-api: IN_PROGRESS (assigned_agent=design-agent-02)
  → 正常执行直至 DONE
```

```
超时场景:
  build-backend: IN_PROGRESS (started_at=10:00:00)
                                        │
                          10:02:00 ────┤
                            task-timeout=120s 到期
                           Watchdog 检测到超时
                           retry_count=0 < 3 → 回滚为 PENDING
                                        │
                          另一 Agent 抢占并快速完成
```

**重试计数**：

```
第 1-2 次失败 → 回滚 PENDING（Watchdog 或 Agent 主动 fail 后重试）
第 3 次失败   → 同上（默认 max_retry=3）
第 4 次失败   → FAILED（超过上限，需要人工介入）
```

重试阈值可通过 `--max-retry` 配置。

### 7. 测试失败反馈闭环

当 `test-e2e` 任务失败时，形成完整的修复-重测闭环。

```
                    ┌──────────────────────┐
                    │    testing Agent      │
                    │  执行 E2E 测试        │
                    └────────┬─────────────┘
                             │ 测试失败
                             ▼
                    ┌──────────────────────┐
                    │ 写反馈到 Message Bus  │
                    │ feedback/user-service │
                    │ status = PENDING_FIX  │
                    └────────┬─────────────┘
                             │
              ┌──────────────┼──────────────┐
              │ 阻塞监听      │              │
              ▼              │              │
    ┌──────────────────┐     │              │
    │  user-service     │     │              │
    │  Agent 轮询到反馈  │     │              │
    │  CAS 认领 → 修复   │     │              │
    │  → FIXED          │     │              │
    └────────┬─────────┘     │              │
             │               │              │
             ▼               ▼              ▼
    ┌──────────────────────────────────────────┐
    │      Aggregator 检测所有反馈 FIXED         │
    │  清除 feedback 记录                       │
    │  将 test-e2e 重置为 PENDING (触发重测)     │
    └──────────────────────────────────────────┘
```

#### 具体操作

**Test Agent**（测试发现失败）：

```bash
# 1. 标记测试任务为 FAILED
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/req-001/tasks/test-e2e/status" -d "FAILED"
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/req-001/tasks/test-e2e/error_message" \
  -d "Login API returns 500 on valid credentials"

# 2. 通过 Message Bus 发送 FIX 请求到 user-service
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/req-001/feedback/user-service/status" -d "PENDING_FIX"
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/req-001/feedback/user-service/payload" \
  -d '{"reporter":"test-agent","error":"Login API returns 500 on valid credentials","severity":"high"}'
```

**Service Agent**（user-service 的 Agent 接收修复请求）：

```bash
# 1. 阻塞监听 feedback（Consul Blocking Query 或 轮询）
while true; do
  STATUS=$(curl -s "http://$CONSUL_ADDR/v1/kv/workflows/req-001/feedback/user-service/status?raw")
  if [ "$STATUS" = "PENDING_FIX" ]; then
    echo "收到修复请求"
    break
  fi
  sleep 5
done

# 2. [必检] ABORT 检查
# 3. 读取反馈详情
curl -s "http://$CONSUL_ADDR/v1/kv/workflows/req-001/feedback/user-service/payload?raw"

# 4. 执行修复（定位 bug → 修改代码 → 提交）
#    ...

# 5. 标记修复完成
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/req-001/feedback/user-service/status" -d "FIXED"
curl -s -X PUT "http://$CONSUL_ADDR/v1/kv/workflows/req-001/feedback/user-service/fix_summary" \
  -d "Fixed NPE in login validation: missing null check on user input"
```

**Aggregator 自动触发重测**：

在每次轮询中检测到所有 `feedback/<service>/status` 均为 `FIXED` 时：

```python
# Aggregator 重测逻辑（伪代码）
for req_id in active_workflows:
    feedback_items = kv_get(f"workflows/{req_id}/feedback/", recurse=True)
    all_fixed = all(item["status"] == "FIXED" for item in feedback_items)

    if all_fixed and len(feedback_items) > 0:
        # 清除 feedback 记录
        kv_delete(f"workflows/{req_id}/feedback/", recurse=True)
        # 重置 test-e2e 为 PENDING 触发重测
        kv_put(f"workflows/{req_id}/tasks/test-e2e/status", "PENDING")
```

**重测循环**：
- Test Agent 轮询发现 `test-e2e` 再次 `PENDING`
- 抢占 → 执行测试
- **成功** → `DONE`，流程继续
- **失败** → 再次发送 FIX（最多 3 次重试）
- **3 次都失败** → `FAILED`，人工介入

### 8. 动态任务提案

Agent 执行过程中发现需要新增任务时（如测试发现需要性能优化），通过 Proposal 机制向人工申请。

```
执行中 Agent 发现遗漏
        │
        ▼
┌──────────────────────┐
│ 读取当前 dependencies │
│ 添加新任务 (perf-opt) │
│ 写入 dependencies     │
│ CAS 设置 status →    │
│   Proposal           │
└────────┬─────────────┘
         │
         ▼
  ┌────────────────┐
  │ Aggregator 暂停 │
  │ 停止推进此需求   │
  └────────────────┘
         │
  ┌──────┴──────┐
  │              │
  ▼              ▼
人工确认        人工拒绝
  │              │
  ▼              ▼
status →     从 deps 删除
CONFIRMED     status →
  │           CONFIRMED
  ▼              │
正常调度         ▼
              正常调度
```

详见 [proposal-protocol.md](./proposal-protocol.md)。

### 9. 流程终止

当所有任务均到达终态时，流程自然终止：

```
所有任务 DONE
  └── Aggregator 不再有可推进的任务（所有任务已终态）
  └── RunManager 检测到全部 DONE → 终止 run，标记 COMPLETED
  └── 看板显示 progress=100%, phase=DONE

某任务失败超过重试上限
  └── Watchdog 标记为 FAILED（retry_count >= max_retry）
  └── 人工通过 WebAPI 介入：修复后重试或 ABORT 整个需求

人工 ABORT
  └── 写入 control=ABORT
  └── 各 Agent 在下次 check-control 时感知并退出
  └── 所有未完成任务标记为 ABORTED
```

---

## 四、Agent 适配指南：一个代码仓如何接入框架

前面的端到端流程从框架视角描述了全过程。本节从**代码仓的 Agent 视角**出发，说明具体需要做什么适配。

### 4.1 适配概述

单个微服务代码仓接入框架，本质上只需要做一件事：**有一个进程能通过 stage-bridge 协议与共享存储交互**。

框架提供两种接入方式：

```
方式 A：worker.py + executor 协议（推荐）
  ┌────────────────────────────────────────┐
  │  worker.py（框架提供的通用 Agent）       │
  │  - 注册 / 心跳 / 轮询 / 抢占 / 完成    │
  │  - 通过 stdin/stdout JSON 调用 executor │
  └────────────┬───────────────────────────┘
               │  stdin: {"req_id", "task_name", "task_meta", "context", "config"}
               │  stdout: {"status": "DONE", "artifacts": {...}}
               ▼
  ┌────────────────────────────────────────┐
  │  executor（你写的实际执行逻辑）          │
  │  - 读 context → 编码 → 写代码 → 测试   │
  └────────────────────────────────────────┘

方式 B：直接使用 stage-bridge 脚本（灵活）
  Agent 进程直接按顺序调用脚本：
  register_agent → heartbeat → claim_task → read_context
  → <实际开发工作> → log_step → write_artifact → complete_task
```

### 4.2 需要设置的环境变量

无论哪种方式，Agent 都需要以下环境变量：

| 变量 | 必填 | 说明 |
|------|:---:|------|
| `AGENT_ID` | 是 | 全局唯一 Agent 标识，如 `worker-user-service-01` |
| `SERVICE_NAME` | 是 | 绑定的微服务名，必须与 `dependencies.json` 中任务的 `service_name` 对应 |
| `CONSUL_ADDR` | 否 | 存储地址，默认 `127.0.0.1:8500`。local-file 模式下不使用 |
| `STAGE_BRIDGE_DIR` | 推荐 | stage-bridge skill 的绝对路径，用于脚本调用 |
| `REPO_PATH` | 推荐 | 代码仓本地路径，worker.py 会传给 executor |
| `CAPABILITIES` | 推荐 | 能力标签，如 `dev,test`，用于任务过滤打分 |

stage-bridge 脚本支持从 `.env` 文件读取这些值，搜索路径依次为：当前工作目录 → skill 目录 → `~/.claude/stage-bridge/`。

### 4.3 方式 A：使用 worker.py（推荐）

无需任何开发工作，只需要写一个 executor。

#### 工作模式

```bash
# 启动 worker（常驻进程）
AGENT_ID=worker-user-service-01 \
SERVICE_NAME=user-service \
python3 skills/stage-bridge/scripts/worker.py \
  --service user-service \
  --capabilities dev \
  --repo-path /path/to/user-service \
  --executor "python3 /path/to/my_executor.py"

# 或单次模式：抢一个任务后就退出
AGENT_ID=worker-user-service-01 \
python3 skills/stage-bridge/scripts/worker.py \
  --service user-service \
  --once
```

Worker 自动完成：

```
register → heartbeat(10s) → find_pending_tasks → rank_tasks → claim_task(CAS)
  → check_control → load_context → execute_task → complete/fail → 回到 find_pending_tasks
```

#### Executor 协议

Executor 是从 worker.py 解耦出来的实际执行逻辑。协议只有两条规则：

**stdin 输入**（worker → executor）：

```json
{
  "req_id": "REQ-001",
  "task_name": "build-backend",
  "task_meta": {
    "type": "backend",
    "service_name": "user-service",
    "description": "实现用户登录接口",
    "capability": "dev",
    "metadata": {}
  },
  "context": {
    "api_spec": "{\"endpoints\": [...]}",
    "pr_url": "https://github.com/..."
  },
  "config": {
    "agent_id": "worker-user-service-01",
    "service_name": "user-service",
    "repo_path": "/path/to/user-service",
    "worktree_base": ".worktree"
  }
}
```

**stdout 输出**（executor → worker）：

```json
{
  "status": "DONE",
  "summary": "实现了登录接口，创建了 PR",
  "artifacts": {
    "branch": "feature/REQ-001-login",
    "pr_url": "https://github.com/...",
    "commit": "abc123"
  }
}
```

`status` 为 `"DONE"` 时 worker 调用 `complete_task`；为 `"FAILED"` 时调用 `fail_task`。

**默认 executor**：如果不指定 `--executor`，worker 使用占位模式，直接把任务标记为 DONE（跳过实际执行），用于测试 worker 循环。

#### 什么情况下需要写 executor

| 场景 | 需要写 executor | 工作量 |
|------|:---:|:---:|
| 想快速测试框架连通性 | 否（占位模式自动 DONE） | 0 |
| 想跑固定脚本（如 `make build && make test`） | 是 | ~10 行 shell 包装 |
| Claude Code / Codex / OpenCode 做实际开发 | 是 | 把 AI 编码工具的调用封装成 executor |
| 代码检查 / 测试执行 / 部署 | 是 | 根据任务类型执行对应的命令 |

executor_placeholder.py（21 行）是最简参考：

```python
# executor_placeholder.py — 只读 stdin，写 stdout
import json, sys
task_input = json.load(sys.stdin)     # 读 worker 传入的 JSON
req_id = task_input["req_id"]
task_name = task_input["task_name"]
context = task_input.get("context", {})

# ... 实际工作 ...

print(json.dumps({                   # 输出结果给 worker
    "status": "DONE",
    "summary": f"任务 {task_name} 完成",
    "artifacts": {"branch": f"hw-{task_name}"}
}))
```

### 4.4 方式 B：直接使用 stage-bridge 脚本

不需要 worker.py，Agent 进程直接调用 stage-bridge 的脚本完成生命周期。这种方式适合嵌入到 Claude Code / Codex / OpenCode 等 AI 编码工具的指令中。

#### 完整生命周期脚本清单

| 阶段 | 脚本 | 说明 |
|------|------|------|
| 注册 | `register_agent.py --service <name> --capabilities <caps>` | 向存储注册自己 |
| 心跳 | `heartbeat.py --loop 10` | 后台运行，每 10s 发一次（用 `nohup` 或 `&`） |
| 抢占 | `claim_task.py <req_id> <task_name>` | CAS 抢占指定任务，退出码 1 表示被抢先 |
| 自动抢占 | `claim_next_task.py` | 自动查找并抢占下一个匹配的任务 |
| 读上下文 | `read_context.py <req_id> [key]` | 读全部或指定 key，支持 `--wait` 阻塞等待 |
| 记录日志 | `log_step.py <req_id> <message>` | 记录一个可感知的步骤到事件流 |
| 写产物 | `write_artifact.py <req_id> <key> <value>` | 默认写到当前任务，`--scope context` 写到全局 |
| 完成任务 | `complete_task.py <req_id> <task_name>` | 标记为 DONE，支持 `--await-review` |
| 标记失败 | `fail_task.py <req_id> <task_name> --error "..."` | 标记为 FAILED 并记录错误 |
| 注销 | `deregister_agent.py` | 退出时注销 |
| Session 管理 | `record_session_start/end.py` | 记录任务的执行会话生命周期 |
| 反馈监听 | `feedback_listen.py <req_id> <service>` | 阻塞等待修复请求 |
| 反馈解决 | `feedback_resolve.py <req_id> <service> --summary "..."` | 标记修复完成 |
| ABORT 检查 | `check_control.py <req_id>` | 检查控制信号，退出码 7 表示 ABORT |

#### Claude Code 适配示例

将 stage-bridge 模板追加到代码仓的 `CLAUDE.md` 中即可。核心流程：

```markdown
## 多 Agent 平台协作协议

本仓库（user-service）受 stage-bridge 多 Agent 开发平台调度。

### 启动
```bash
python $STAGE_BRIDGE_DIR/scripts/register_agent.py --capabilities backend --service user-service
nohup python $STAGE_BRIDGE_DIR/scripts/heartbeat.py --loop 10 > /tmp/hb.log 2>&1 &
```

### 任务执行
```bash
# 1. 抢占（exit 1 = 被抢先，直接结束）
python $STAGE_BRIDGE_DIR/scripts/claim_task.py "$REQ_ID" "$TASK_NAME"

# 2. 读上游产物
python $STAGE_BRIDGE_DIR/scripts/read_context.py "$REQ_ID"

# 3. 编码...（Read / Edit / Write / Bash）

# 4. 记录步骤
python $STAGE_BRIDGE_DIR/scripts/log_step.py "$REQ_ID" "通过单元测试"

# 5. 写产物
python $STAGE_BRIDGE_DIR/scripts/write_artifact.py "$REQ_ID" pr_url "$PR_URL" --scope context

# 6. 完成
python $STAGE_BRIDGE_DIR/scripts/complete_task.py "$REQ_ID" "$TASK_NAME"
```

### 硬性约束
- 未抢占任务绝不开始编码
- 绝不直接 push 到 main
- 绝不跳过 complete_task 或 fail_task
```

Codex CLI 和 OpenCode 有对应的模板在 `skills/stage-bridge/templates/` 目录下。

### 4.5 适配清单总结

一个代码仓接入框架，按顺序检查以下事项：

| # | 事项 | 说明 | 必须 |
|:--|------|------|:---:|
| 1 | **确定微服务名** | `SERVICE_NAME` 是什么？必须与 `dependencies.json` 中的 `service_name` 对齐 | 是 |
| 2 | **设置环境变量** | `AGENT_ID`、`SERVICE_NAME`、`CONSUL_ADDR`、`STAGE_BRIDGE_DIR` | 是 |
| 3 | **选接入方式** | worker.py + executor（推荐），或直接使用 stage-bridge 脚本 | 是 |
| 4 | **写 executor（如选方式 A）** | 实现 stdin/stdout JSON 协议，执行实际开发工作 | 按需 |
| 5 | **配 CLAUDE.md / 指令文件**（如选方式 B） | 追加 stage-bridge 模板到 AI 编码工具的指令中 | 按需 |
| 6 | **启动 Agent** | 运行 worker.py 或手动执行脚本 | 是 |
| 7 | **验证连通** | 确认 Agent 注册成功、心跳正常、能抢占 PENDING 任务 | 是 |

### 4.6 框架提供的能力 vs 代码仓需要做的

```
框架提供：                                   代码仓需要做：
┌────────────────────────────┐              ┌──────────────────────┐
│  stage-bridge 脚本 22 个   │              │  AGENT_ID            │
│  worker.py 常驻循环        │              │  SERVICE_NAME        │
│  executor 协议定义         │              │  executor（或不用）   │
│  Claude Code / Codex /    │              │  CLAUDE.md 配置       │
│  OpenCode 模板            │              │  .env 或环境变量      │
│  三种存储后端兼容          │              └──────────────────────┘
│  心跳 / 注册 / 抢占 / CAS │
│  check_control / ABORT    │
│  feedback 监听 / 解决     │
└────────────────────────────┘
```

**接入结论**：框架已经把 Agent 与存储交互的完整协议封装成 22 个脚本 + 1 个 worker 循环 + 3 个平台模板。一个代码仓接入框架不需要写任何框架相关的代码，只需要设置好环境变量，决定用 worker.py 还是直接调用脚本。（worker.py 的 executor 可以根据需要选择写或不写。）

---

## 五、三种模式在端到端流程中的差异

| 流程步骤 | Consul 模式 | Local 模式 | Local-File 模式 |
|----------|-------------|------------|-----------------|
| **启动 Consul/存储** | `./scripts/start_consul_dev.sh` | 无需操作（框架内嵌） | 无需操作 |
| **启动框架** | `python -m harness_framework.daemon` | `--local` | `--local-file` |
| **初始化需求** | `sync_to_consul.py`（同） | 同上 | 同上 |
| **Agent 注册** | `curl → PUT /v1/agent/service/register` | 同一 curl 命令 | `file_kv.py register` |
| **Agent 心跳** | `curl → PUT /v1/agent/check/pass/` | 同一 curl 命令 | `file_kv.py heartbeat` |
| **任务抢占** | `curl → PUT ?cas=<index>` | 同一 curl 命令 | `file_kv.py put --cas` |
| **读写数据** | `curl → GET/PUT /v1/kv/` | 同一 curl 命令 | `file_kv.py get/put` |
| **Blocking Query** | Consul 原生长轮询 | 0.5s 轮询模拟 | 0.5s 轮询模拟 |
| **ABORT 检测** | `curl → GET /v1/kv/.../control?raw` | 同一 curl 命令 | `file_kv.py get` |
| **消息总线** | `curl → PUT /v1/kv/.../feedback/` | 同一 curl 命令 | `file_kv.py put` |
| **WebAPI 看板** | 框架 8080 端口（三模式共享） | 框架 8080 端口 | 框架 8080 端口 |
| **查看存储数据** | Consul UI :8500/ui | 不适用（HTTP 无 UI） | 直接查看 JSON 文件 |

**核心结论**：

- **Consul 模式和 Local 模式对 Agent 完全透明**——Agent 使用完全相同的 curl 命令，只需要改 `CONSUL_ADDR` 环境变量
- **Local-File 模式的 Agent 必须使用 `file_kv.py` CLI**，不能使用 curl
- **框架三大组件在三种模式下行为完全一致**——存储抽象层保证了框架逻辑与存储解耦
- **推荐路径**：开发阶段用 `--local-file` → 需要 Agent 调试时用 `--local` → 生产部署用 Consul

---

## 五、FAQ

**Q: 三种模式的数据可以互相迁移吗？**

可以。Local 模式指定 `--local-data-file` 后生成的 JSON 文件与 Local-File 模式的 JSON 文件格式兼容。从 Local/Consul 迁移到另一种模式时，只需重新初始化需求（`sync_to_consul.py`），或手动导出/导入数据。

**Q: Local 模式的内嵌 HTTP 服务器可靠吗？**

适合开发和测试。它是单进程 `ThreadingHTTPServer`，生产级别的高可用需要 Consul 集群的 Raft 共识和多节点故障转移。

**Q: Local-File 模式下文件锁竞争会影响性能吗？**

在单节点、少量 Agent（< 10）的场景下完全够用。`fcntl.flock` 是操作系统级别的文件锁，每次操作耗时通常在毫秒级。大规模并发需要 Consul 模式。

**Q: Agent 如何知道当前是哪种存储模式？**

Agent 不需要知道。对于 Consul 和 Local 模式，Agent 只需配置 `CONSUL_ADDR` 指向正确的 HTTP 地址。对于 Local-File 模式，Agent 需要改用 `file_kv.py` CLI 并指定 `--data-file`。

**Q: 框架主进程可以同时处理多个 workflow 吗？**

可以。同一个框架实例可以管理任意多个 workflow（`req-001`、`req-002`、...），每个 workflow 在 KV 中以 `workflows/<req_id>/` 前缀隔离。Aggregator 会扫描并推进所有 `published=true` 的 workflow。

**Q: 如何选择合适的模式？**

- 只想快速看框架跑起来：`--local-file`
- 需要 Agent 参与调试（想用 curl）：`--local`
- 生产环境或多人协作：Consul 模式 + 3 节点集群
- CI 环境验证框架行为：`--local` 或 `--local-file`

**Q: 任务之间的数据怎么传递？B 任务依赖 A 任务的输出，框架管吗？**

框架**不管**。框架只负责执行顺序（`depends_on` 全部 DONE 后激活下游为 PENDING）。数据传递是 Agent 自己的事：

- A Agent 完成任务后，把产物写到 `workflows/<req_id>/context/<key>`（全局可见）
- B Agent 认领任务后，调用 `read_context.py <req_id>` 读取上游产物
- 推荐习惯：A Agent 先写产物再标记 DONE，确保 B 被激活时数据就绪
- 如果顺序没保证，B 可以用 `read_context.py --wait <key>` 阻塞等待

详见第三章"数据传递机制"。

**Q: Agent 可以删除数据文件吗？**

在 local-file 模式下，Agent **不能**删除数据文件本身。`file_kv.py` CLI 没有删除文件的命令，只能操作文件内的 KV 条目（`delete <key> --recurse`）。即便清空了所有 KV 数据，JSON 文件本身依然存在。

在 Consul / Local 模式下，Agent 可以通过 `DELETE /v1/kv/<key>?recurse=true` 递归删除 KV 条目，但不能删除 Consul 服务端文件或 LocalStore 的持久化文件。

**Q: local-file 模式下 Agent 可以删除任务吗？**

可以删除任务 KV 数据，但不能从 DAG 中移除任务。

- `file_kv.py delete workflows/req-001/tasks/design-api --recurse` 会清除该任务的所有状态 KV（status、type、assigned_agent 等）
- 但 `dependencies` JSON 中该任务的条目还在，Aggregator 读到 status="" 会视为"未初始化"，如果依赖满足会重新激活为 PENDING。**删了等于重置，不是彻底移除**
- 真正从 DAG 中删除任务需要人工修改 `dependencies` JSON（通过 `sync_to_consul.py --force` 或在 Proposal 流程中重写）

**Q: 生成 dependencies.json 依赖什么？**

依赖的是**对系统架构的认识**，不是依赖框架运行。三种生成方式：

1. **AI 辅助**：设计 Agent（如 Claude）读设计文档后自动生成
2. **结构化解析**：`design-pipeline` 从带标记的设计文档中提取
3. **手动编写**：人直接写 JSON，不依赖任何工具

核心决定因素是**微服务划分**——每个任务分配哪个 `service_name`，取决于"这个功能属于哪个微服务"。虚拟服务（`_design`、`_test`、`_deploy`）用于跨服务角色。

**Q: Agent 注册之后，框架怎么知道哪个需求分配给哪个 Agent？**

框架**不分配**。这是设计原则（第四条：执行层主动认领任务）。

- 每个任务在 `dependencies.json` 中有 `service_name` 标签
- 每个 Agent 启动时声明自己的 `SERVICE_NAME`
- Agent 轮询时扫所有 PENDING 任务，按 `service_name` 匹配度打分（完全匹配 +500，不匹配 -100），取最高分 CAS 抢占
- Aggregator 全程不知道哪个 Agent 会做哪个任务，它只做 BLOCKED → PENDING

**Q: 一个代码仓的 Agent 接入框架需要做什么适配？**

零代码修改，只需要配置。框架提供了三种平台模板（Claude Code / Codex / OpenCode）和两套接入机制：

- **worker.py（推荐）**：框架提供的通用 Agent 循环，代码仓只需要写一个 executor（按 stdin/stdout JSON 协议执行实际工作）
- **直接使用 stage-bridge 脚本**：在 AI 工具的指令文件中追加生命周期脚本调用

代码仓需要做的是：
1. 确定 `SERVICE_NAME`
2. 设置环境变量（`AGENT_ID`、`SERVICE_NAME`、`CONSUL_ADDR`）
3. 选择接入方式并配置
4. 启动 Agent

详见第四章"Agent 适配指南"。

---

**相关文档**：

- [使用指南](./usage-guide.md) — 快速上手指南
- [状态机定义](./status-state-machine.md) — 任务状态流转规则
- [Message Bus](./message-bus.md) — 任务间消息通信详解
- [动态任务提案](./proposal-protocol.md) — 动态任务提案协议
- [Agent 重试模式](./agent-retry-pattern.md) — 重试策略详解
- [记忆模型](./memory-model.md) — 共享上下文记忆设计
- [架构文档](architecture.md) — 整体架构设计
