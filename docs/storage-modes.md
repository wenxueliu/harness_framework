# 存储模式详解

> **初次接触？** 先看 [quickstart.md](quickstart.md) 和 [concepts.md](concepts.md)。本文是三种存储后端的深度对比。

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

## 三、端到端流程（各模式差异）

完整的协作流程（需求定义 → 启动 → Agent 抢占 → 执行 → 故障恢复 → 反馈闭环 → 终止）见 [architecture.md](architecture.md) 第五章。入门教程见 [getting-started.md](getting-started.md)。

以下仅列出各存储模式在关键步骤上的**命令差异**：

### 启动框架

| 模式 | 命令 |
|------|------|
| Consul | `./scripts/start_consul_dev.sh`（先启 Consul）；`python -m harness_framework.daemon` |
| Local | `python -m harness_framework.daemon --local` |
| Local-File | `python -m harness_framework.daemon --local-file` |

### 初始化需求

三种模式使用相同的 `sync_to_consul.py` 命令，无需区分。

### Agent 注册

| 模式 | 命令 |
|------|------|
| Consul / Local | `curl -X PUT .../v1/agent/service/register` |
| Local-File | `python scripts/file_kv.py --data-file ... register '...'` |

### Agent 心跳

| 模式 | 命令 |
|------|------|
| Consul / Local | `curl -X PUT .../v1/agent/check/pass/service:<id>` |
| Local-File | `python scripts/file_kv.py --data-file ... heartbeat <id>` |

### 任务抢占（CAS）

| 模式 | 命令 |
|------|------|
| Consul / Local | `curl -X PUT .../v1/kv/.../status?cas=<idx> -d IN_PROGRESS` |
| Local-File | `python scripts/file_kv.py --data-file ... put .../status IN_PROGRESS --cas <idx>` |

### ABORT 检测

| 模式 | 命令 |
|------|------|
| Consul / Local | `curl .../v1/kv/.../control?raw` |
| Local-File | `python scripts/file_kv.py --data-file ... get .../control` |

> Agent 接入的完整指南见 [agent-guide.md](agent-guide.md)。故障恢复、反馈闭环、动态任务提案等机制见 [architecture.md](architecture.md)。

---

## 四、Agent 适配：不同模式下 Agent 接入要点

Agent 接入的完整教程见 [agent-guide.md](agent-guide.md)。本节仅说明存储模式在选择接入方式时的影响。

- **Consul / Local 模式**：Agent 使用 `stage-bridge` Skill（HTTP 通信），注册 → 心跳 → 抢占 → 执行 → 完成 → 注销的完整生命周期
- **Local-File 模式**：Agent 使用 `file-kv` Skill（CLI 直接读写 JSON），无需注册和心跳，Watchdog 默认 Agent 始终存活

核心结论：Consul 和 Local 模式对 Agent **完全透明**（相同的 curl 命令，只需改 `CONSUL_ADDR`）。Local-File 模式需要改用 `file_kv.py` CLI。

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

## 六、FAQ

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

> 其他常见问题见 [faq.md](faq.md)。

## 相关文档

| 我想… | 看这里 |
|-------|--------|
| 回顾核心概念 | [concepts.md →](concepts.md) |
| 查看常见操作命令 | [usage-guide.md →](usage-guide.md) |
| 了解状态机定义 | [status-state-machine.md →](status-state-machine.md) |
| 了解消息通信 | [message-bus.md →](message-bus.md) |
| 了解动态任务提案 | [proposal-protocol.md →](proposal-protocol.md) |
| 了解重试策略 | [agent-retry-pattern.md →](agent-retry-pattern.md) |
| 了解记忆模型 | [memory-model.md →](memory-model.md) |
| 查看架构设计 | [architecture.md →](architecture.md) |
