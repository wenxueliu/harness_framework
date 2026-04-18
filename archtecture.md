# 多 Agent 软件开发平台 · 最终完整方案（v4.1）

---

## 目录

第一章 总体架构与设计原则
第二章 框架层组件设计
第三章 执行层与 stage-bridge Skill
第四章 Consul KV 数据模型
第五章 核心协作流程
第六章 P0-1 反应循环防护机制（含全链路 ABORT 检测）
第七章 P0-2 Generator-Verifier 自验证机制（执行层自治）
第八章 P1-1 DAG 扩展（Parallelization / Aggregate）
第九章 业务看板
第十章 实施计划与里程碑

---

## 第一章　总体架构与设计原则

### 1.1 平台定位

本平台是一个**面向软件开发场景的多 Agent 协作框架**，目标是让若干编码智能体（Codex、OpenCode、Claude Code 等）在各自绑定的微服务代码仓库上协同完成一个需求的完整研发流程，涵盖需求设计、设计评审、代码开发、代码评审、重构、测试、部署等阶段。平台本身不替代编码智能体的"生成能力"，而是为它们提供**调度骨架、状态管理、协作契约与安全护栏**。

平台严格区分**框架层**与**执行层**。框架层由平台团队负责维护，提供通用的依赖推进、状态存储、监控、看板能力；执行层由业务团队自由组合，任何符合 stage-bridge Skill 契约的编码智能体都能接入。这种分层让"平台能力"与"开发实践"解耦，平台升级不影响执行层、执行层定制也不污染平台。

### 1.2 协作模式定位

对照 Anthropic 与 Claude 总结的五种协作模式[1] [2]，本平台的最终架构是**以 Shared State 为骨、轻量 Orchestrator 仅做依赖推进、在执行层嵌入 Generator-Verifier**的混合体。Consul KV 充当权威共享状态存储，承担 Agent 间的隐式协调；Aggregator 仅做"依赖推进"这一件事，不做能力匹配、不分配 Agent，更不参与任何 LLM 决策；每个开发 Agent 在内部自主决策是否进行 Generator-Verifier 自验证。

这种定位的核心考量是**避免过度设计**。Anthropic 文章反复强调"起步从简、按需演进"[1]，本平台在 MVP 阶段只实现骨架与一项必备增强（P0 反应循环防护），自验证由执行层自治推进，DAG 表达能力扩展为 P1，知识沉淀与事件总线则留待实际瓶颈出现后再实施。

### 1.3 设计原则

平台的七条基本设计原则贯穿所有组件。**第一条是 Consul 作为唯一权威状态源**，所有 Agent 的协作都通过 Consul KV 完成，框架本身不保留任何进程内状态，这样 Agent 可以随意重启、迁移、并行扩容。**第二条是 CAS 原子操作保护竞态**，任务抢占、feedback 认领、预算扣减等涉及多实例竞争的写操作一律使用 Consul 的 Check-And-Set，杜绝任何形式的"读-改-写"非原子路径。**第三条是框架层不做 LLM 调用、不做 Agent 分配**，Aggregator、Watchdog 全部是无智能的规则引擎，LLM 调用与执行决策完全下沉到执行层，框架可以跑在极低配置的机器上，且行为完全可预测。

**第四条是执行层主动认领任务**，每个 Agent 与服务是 1:1 强绑定，绑定关系在 `dependencies.json` 的 `service_name` 字段静态声明；Agent 启动后主动轮询自己服务名下的 `PENDING` 任务并 CAS 抢占，框架不写"分配提示"。**第五条是全链路 ABORT 检测**，每个 Agent 在 LLM 调用前后、verify 循环每轮、feedback-listen 每次唤醒时都必须显式调用 `check-control`，收到 ABORT 立即干净退出，杜绝"框架已停、Agent 还在跑"的脏状态。

### 1.4 架构全景

从物理视角看，平台在一台开发机上运行以下进程：一个 Consul dev mode 实例，一个框架主进程 daemon（内含 Aggregator、Watchdog、WebAPI、BudgetGuard 四个模块），若干人工启动的 Agent 进程（每个绑定一个微服务代码仓），以及一个 React 业务看板。Agent 通过 HTTP 与 Consul 通信，看板通过 HTTP 与 Consul 及框架 WebAPI 通信，框架 daemon 通过 HTTP 与 Consul 通信。整个平台 MVP 阶段对外只暴露两个端口：Consul 的 8500 与框架 WebAPI 的 8600。

---

## 第二章　框架层组件设计

框架层是一个单进程 Python 应用，通过多线程协作承载四个核心模块。合并到单进程的理由是 MVP 阶段的状态规模不大（单机数十个 Agent、数百个任务），单进程在运维、日志、调试上都远比多进程简单；未来真需要拆分时，四个模块已经按照清晰的接口隔离，拆分成本很低。

### 2.1 Aggregator 模块（仅做依赖推进）

Aggregator 是 DAG 依赖推进引擎，**职责被严格收敛到一件事**：扫描 DAG，发现某任务的所有上游依赖均已 `DONE` 时，将该任务的 `phase` 从 `WAITING` 推进到 `PENDING`。Aggregator **不做能力匹配，不写 `assigned_agent_hint`，不参与任何 Agent 选择**——任务的归属在 `dependencies.json` 的 `service_name` 字段就已经声明清楚，由对应服务的 Agent 主动来抢占。

Aggregator 还负责两类辅助推进。其一是 **feedback 闭环检测**：当 `test-e2e` 任务处于 `FAILED` 且所有 `feedback/<service>/status` 均为 `FIXED` 时，自动清除 feedback 记录并将 `test-e2e` 重置为 `PENDING` 以触发重测。其二是 **parallel/aggregate 节点推进**：parallel 节点上游全部完成时将其 children 全部置为 `PENDING`，aggregate 节点上游 parallel 全部 `DONE` 时将其自身置为 `DONE` 并推进下游，详见第八章。

Aggregator 自身不持久化任何状态，所有判断依据都来自 Consul KV，重启后立即恢复工作。多实例 Aggregator 通过 Consul Leader Election 选主，同一时刻只有 Leader 做写入，避免重复推进。Aggregator 同样**遵守 ABORT 信号**——当 `workflows/<req_id>/control.signal == ABORT` 时，立即停止对该需求的所有推进。

### 2.2 Watchdog 模块

Watchdog 是僵尸任务回收器。它每 30 秒扫描所有 `IN_PROGRESS` 状态的任务，对比 `tasks/<name>/heartbeat_at` 与当前时间，若超过 `heartbeat_timeout`（默认 120 秒），则通过 CAS 将任务状态回退到 `PENDING`，并将 `retry_count` 加一。当 `retry_count` 超过 `max_retry`（默认 3 次）时，Watchdog 将任务标记为 `FAILED` 并写入告警到 `alerts/<req_id>/<task>`，等待人工介入。

### 2.3 WebAPI 模块

WebAPI 基于 FastAPI 实现，监听 8600 端口，为业务看板和 CI 工具提供统一的 HTTP 接口。主要接口包括：`
GET /api/workflows` 返回所有需求的摘要列表，`
GET /api/workflows/<req_id>` 返回指定需求的完整 DAG 与任务状态，`
POST /api/workflows/<req_id>/control` 写入控制信号（PAUSE / RESUME / ABORT / RETRY），`
GET /api/sessions/<req_id>/<task>` 返回任务的 Session 事件流，`
所有接口都是对 Consul KV 的只读或有限写入封装，WebAPI 自身不保留任何状态。

WebAPI 默认允许跨域，看板可以直接从浏览器调用。对于写入类接口，通过 Consul ACL Token 控制权限，敏感操作（如 ABORT）需要额外的审批字段。

---

## 第三章　执行层与 stage-bridge Skill

### 3.1 执行层接入原理

执行层指所有在平台上运行的 Agent。每个 Agent 本质上是一个驱动编码智能体的轻量进程，它**主动**从 Consul KV 拉取本服务的任务、调用编码智能体生成产出、将产出写回 Consul KV。平台不限制 Agent 用什么语言实现、也不限制驱动哪种编码智能体，只要求 Agent 使用 stage-bridge Skill 与 Consul 交互。

stage-bridge Skill 是一组命令行脚本的集合，编码智能体（或 Agent 进程）通过执行 `python -m stage_bridge.<command> <args>` 来完成状态读写。这种"命令行桥接"方式的好处是天然跨语言、天然适配 CLI 型编码智能体、也便于人工调试（直接在终端跑命令可以模拟 Agent 行为）。

### 3.2 Agent 与服务的绑定关系

每个 Agent 在启动时通过 `register-agent --service user-service --capability dev` 向 Consul 声明自己绑定的微服务名称与能力标签。这是一个**人工启动、人工绑定**的过程：开发者明确知道"我在哪台机器上为哪个微服务启了一个开发 Agent"，框架不主动拉起 Agent。绑定关系是 1:1 的——同一时刻同一个服务原则上只有一个开发 Agent；如果出现多实例（例如冗余部署），CAS 抢占会保证同一任务只被执行一次。

任务的归属在 `dependencies.json` 设计阶段就由设计 Agent 或人工填写明确，每个任务节点带有 `service_name` 字段。Agent 启动后，通过 stage-bridge 的 `claim-task --service user-service` 命令主动轮询自己服务名下的 `PENDING` 任务，发现就尝试 CAS 抢占，无任务时按指数退避继续轮询。

### 3.3 stage-bridge 命令清单

stage-bridge 在 v3 基础上新增两条命令支持 P0 反应循环防护，删除原 v4 草案中的 `verify-output` 和 `load-knowledge`。完整命令清单如下表。

| 命令 | 用途 | 引入版本 |
| :--- | :--- | :--- |
| `register-agent` | Agent 启动时注册到 Consul，声明 service_name 与 capability | v1 |
| `deregister-agent` | Agent 退出时主动注销 | v1 |
| `heartbeat` | 维持 Consul TTL Check，防止被标记为僵尸 | v1 |
| `claim-task` | 按 service_name 主动轮询并 CAS 抢占 PENDING 任务 | v1（v4.1 强化） |
| `read-context` | 读取上游 Agent 写入的上下文（服务端点、API 契约等） | v1 |
| `write-artifact` | 写入本任务产出到 KV（含写入指纹去重） | v1（v4 强化） |
| `log-step` | 记录任务执行事件到 Session 事件流（含写入指纹去重） | v1（v4 强化） |
| `complete-task` | 任务完成，状态置为 DONE | v1 |
| `fail-task` | 任务失败，状态置为 FAILED，写入错误详情 | v1 |
| `feedback-write` | 测试 Agent 写入失败反馈给具体服务 | v1 |
| `feedback-listen` | 服务 Agent 阻塞监听反馈（Blocking Query） | v1 |
| `feedback-resolve` | 服务 Agent 完成修复后写入 FIXED | v1 |
| `check-control` | **检查当前需求/任务的控制信号（ACTIVE / PAUSED / ABORTED）** | v1  |

### 3.4 stage-bridge 调用契约

所有命令统一遵循五条调用契约。
**第一条是从环境变量读取配置**：`CONSUL_ADDR`、`CONSUL_TOKEN`、`AGENT_ID`、`REQ_ID`、`TASK_NAME` 由 Agent 启动脚本注入，命令本身不需要参数也不需要配置文件。
**第二条是统一的 JSON 输出**：所有命令在 stdout 输出 `{"ok": true/false, "data": {...}, "error": "..."}`，stderr 仅输出调试日志，便于编码智能体解析。
**第三条是幂等**：所有写入命令基于 CAS 或 `ifNotExists` 语义，重复调用不会产生副作用。
**第四条是显式超时**：所有命令默认 30 秒超时，`feedback-listen` 类阻塞命令可通过 `--timeout` 参数延长到 10 分钟。
**第五条是错误码规范**：0 = 成功，1 = 通用错误，2 = 参数错误，3 = Consul 连接错误，4 = CAS 冲突（可重试），5 = 权限拒绝，6 = 预算超限，**7 = ABORT 信号生效**。

### 3.5 SKILL.md 强制规则

stage-bridge 的 `SKILL.md` 在 Prompt 模板中以醒目方式强制约束编码智能体的执行循环必须满足以下规则。

> **规则一（必检 ABORT）**：在每次进行 LLM 推理前后、verify 循环每一轮、feedback-listen 每次唤醒时，**必须**先调用 `check-control`。若返回 `ABORTED` 或 `signal=ABORT`，立即调用 `fail-task --reason aborted` 后干净退出，不得执行任何后续操作。
>
---

## 第四章　Consul KV 数据模型

### 4.1 命名空间总览

Consul KV 的顶层命名空间采用业务语义分区，每个需求 `req_id` 的数据都聚合在 `workflows/<req_id>/` 下以便整体清理；跨需求共享的资源（如 Agent 注册表）则放在顶层命名空间。完整布局如下表（已删除 v4 草案的 knowledge / convergence_curator 路径）。

| 路径 | 用途 | 读写方 | 引入版本 |
| :--- | :--- | :--- | :--- |
| `workflows/<req_id>/meta` | 需求元数据（标题、提交人、创建时间） | CI 写、所有组件读 | v1 |
| `workflows/<req_id>/dag` | 任务依赖图（含 task / parallel / aggregate 节点） | CI 写、Aggregator 读 | v1（v4.1 扩展） |
| `workflows/<req_id>/tasks/<task>` | 单个任务的状态、负责 Agent、心跳、service_name | Agent 写、Aggregator 读 | v1 |
| `workflows/<req_id>/context/services/<svc>` | 服务端点、版本、健康检查 URL | 后端 Agent 写、测试 Agent 读 | v1 |
| `workflows/<req_id>/context/api/<svc>` | API 契约 URL 与版本 Hash | 设计 Agent 写、开发 Agent 读 | v1 |
| `workflows/<req_id>/feedback/<svc>` | 测试失败归因反馈 | 测试 Agent 写、服务 Agent 读 | v1 |
| `workflows/<req_id>/sessions/<task>/<sid>` | Session 事件流（含 verify 上报、ABORT 退出记录） | 任意 Agent 写、看板读 | v1 |
| `workflows/<req_id>/control` | 控制信号（PAUSE/RESUME/ABORT/RETRY） | 看板写、Agent 读 | v1（v4.1 强化） |
| `agents/<agent_id>` | Agent 注册信息（含 service_name、capability、负载） | Agent 自写、Aggregator 读 | v1 |

### 4.2 关键数据结构

以下用 JSON Schema 的简化形式描述几个核心路径的数据结构。`workflows/<req_id>/tasks/<task>` 的 Value 结构为：

```json
{
  "phase": "WAITING|PENDING|IN_PROGRESS|DONE|FAILED|PAUSED|SKIPPED",
  "type": "task|parallel|aggregate",
  "service_name": "user-service",
  "capability": "dev",
  "depends_on": ["design"],
  "assigned_agent": "agent-user-service-01",
  "started_at": "ISO8601",
  "heartbeat_at": "ISO8601",
  "retry_count": 0,
  "last_error": "...",
  "artifact_url": "..."
}
```

`workflows/<req_id>/control` 的 Value 结构为：

```json
{
  "signal": "ACTIVE|PAUSE|RESUME|ABORT",
  "scope": "all|task:<name>",
  "issued_by": "human:zhangsan|system:budget_guard",
  "issued_at": "ISO8601",
  "reason": "manual abort due to design change"
}
```

---

## 第五章　核心协作流程

### 5.1 正常流程

一个需求从提交到交付的完整路径如下。CI 在收到新需求后，调用 `sync_to_consul.py` 将 `dependencies.json` 解析并写入 `workflows/<req_id>/dag`，将所有叶子任务（无依赖的任务）的 `phase` 置为 `PENDING`，其余任务置为 `WAITING`。

每个微服务对应的 Agent 早已由人工启动并通过 `register-agent` 在 Consul 中登记。Agent 在循环中**主动调用** `claim-task --service user-service`，stage-bridge 内部按以下步骤执行：列出 `workflows/*/tasks/*` 中 `service_name == user-service` 且 `phase == PENDING` 的任务、对每个候选任务尝试 CAS 抢占（将 `phase` 从 `PENDING` 改为 `IN_PROGRESS` 并写入 `assigned_agent`）、抢占成功则返回任务详情。Agent 拿到任务后，先调用 `check-control` 确认未被 ABORT，再调用 `read-context` 读取上游产出，将其注入编码智能体的 System Prompt。

编码智能体生成代码后，开发 Agent 自行决定是否运行 `verify`（lint / type-check / unit test），结果通过 `log-step --type verify` 上报到 Session 事件流。每次重要的 LLM 推理前，必须调用 `check-control` 检查 ABORT。代码完成后，Agent 调用 `write-artifact` 写入产物（PR URL、服务端点等），调用 `complete-task` 将状态置为 `DONE`，Aggregator 检测到状态变更后推进下游任务。

### 5.2 测试失败反馈流程

测试 Agent 在执行 E2E 用例失败后，通过 `feedback-write` 命令将失败信息按归因规则写入 `workflows/<req_id>/feedback/<svc>`。每个绑定微服务的开发 Agent 通过 `feedback-listen` 阻塞监听对应路径，循环结构为"`feedback-listen` 唤醒 → `check-control` 检查 ABORT → CAS 认领 → 执行修复 → `feedback-resolve` 写入 FIXED"。Aggregator 在每次推进循环中检查当前需求的所有 feedback 状态，当全部为 `FIXED` 时清除 feedback、重置 `test-e2e` 为 `PENDING`，触发重新测试。

### 5.3 ABORT 信号传播流程（v4.1 强化）

ABORT 信号可由人工通过看板下发，也可由 BudgetGuard 在预算超限时下发。Watchdog 监听 `control.signal` 变更，将其传播到 `workflows/<req_id>/tasks/*/control` 的快照。所有正在执行的 Agent 在执行循环的关键节点显式调用 `check-control`，发现 `ABORTED` 后立即调用 `fail-task --reason aborted` 并退出执行循环。stage-bridge 的 `feedback-listen`、`claim-task` 等阻塞命令内部也会周期性检查 ABORT 信号，超过阈值即返回错误码 7 让 Agent 主进程感知。这样保证 ABORT 后最长 1-2 秒内整个需求下所有 Agent 都能干净退出。

---

## 第六章　P0-1　反应循环防护机制（含全链路 ABORT 检测）

### 6.1 问题背景

Anthropic 文章在 Shared State 模式的局限中明确指出：**Reactive Loop** 是该模式最致命的风险[1]。Agent A 写入某个值触发 Agent B，B 的响应又触发 A，两者在没有终止条件的情况下可能在几分钟内吃光 LLM 配额。在我们的场景中，最典型的反应循环是"测试 Agent 报错 → 开发 Agent 修复 → 测试 Agent 仍然报错 → 开发 Agent 再次修复 → ..."，如果修复逻辑始终无法收敛到真正的根因，循环可以持续数百轮。与此并存的另一类风险是 ABORT 信号下发后 Agent 不感知，框架以为已停而 Agent 还在烧钱。

### 6.2 防护机制

本平台通过全链路 ABORT 检测，强制要求 Agent 在执行循环的关键节点显式查询控制信号，确保人工或系统下发的 ABORT 能被实时感知并响应。

#### 6.2.1 全链路 ABORT 检测（v4.1 新增）

ABORT 检测要求**每个 Agent 在执行循环的所有关键节点都必须显式调用 `check-control`**，不允许"埋头执行直到任务结束"的写法。具体的检测节点清单如下：

| 节点 | 触发时机 | 收到 ABORT 后的动作 |
| :--- | :--- | :--- |
| **LLM 调用前** | 每次准备调用编码智能体推理之前 | 不发起本次调用，转入退出流程 |
| **LLM 调用后** | 编码智能体返回结果之后、写入 KV 之前 | 丢弃返回结果，转入退出流程 |
| **verify 循环每轮** | 自验证每次开始前 | 跳出 verify 循环，转入退出流程 |
| **feedback-listen 唤醒后** | Blocking Query 返回时 | 不处理本次反馈，转入退出流程 |
| **claim-task 抢占前** | 已选定候选任务、CAS 写入前 | 放弃抢占，让出任务 |
| **心跳周期** | 每 30 秒一次 heartbeat | 触发主退出流程 |

退出流程统一为：调用 `fail-task --reason aborted` 写入失败原因 → 调用 `log-step --type aborted` 记录退出时间戳 → 调用 `deregister-agent`（如果 Agent 设计为单任务进程）或回到 `claim-task` 主循环（如果 Agent 设计为常驻进程，等待恢复后再领新任务）。`check-control` 命令本身极轻量（一次 Consul KV GET，通常 < 5ms），不会成为性能瓶颈。

stage-bridge 的 SKILL.md 在 Prompt 模板中以醒目格式列出上述节点清单，并提供 Codex / OpenCode / Claude Code 三种主流编码智能体的接入示例，确保编码智能体在生成 Agent 执行流程时不会遗漏。

### 6.3 配置示例

`dependencies.json` 顶层新增 `guardrails` 字段控制四层防护的参数，示例如下：

```json
{
  "req_id": "req-042",
  "title": "新增用户登录流程",
  "guardrails": {
    "abort_check_interval_seconds": 30
  },
  "tasks": [...]
}
```

未配置 `guardrails` 时使用框架默认值。字段省略即采用默认，无需全部填写。`abort_check_interval_seconds` 控制阻塞类命令（如 `feedback-listen`）内部的 ABORT 轮询频率。

---

## 第七章　P0-2　Generator-Verifier 自验证机制（执行层自治）

### 7.1 机制定位与归属

Anthropic 文章引用实战数据：**Generator-Verifier 循环可将 LLM 输出一次通过率从约 60% 提升到 90% 以上**[1]。本平台**将自验证完全归属执行层**——是否做、何时做、做几轮、用什么工具，由开发 Agent 根据当前任务和服务的实际情况自行决策。框架既不解析任何 `verify.yaml`，也不提供 `verify-output` 命令。这种归属选择有三个理由。

其一是**避免框架变重**。验证规则千差万别（前端 vs 后端、TypeScript vs Java、单元测试 vs 静态分析），平台一旦把规则抽象出来就要被迫维护一套小型 DSL，复杂度急剧上升。其二是**尊重服务自治**。每个微服务的代码仓里早已有自己成熟的 lint / test 命令（在 package.json scripts、Makefile、CI 配置里），让开发 Agent 直接调用现成命令比另搞一套元配置更直接。其三是**保留灵活性**。某些任务（如纯文档撰写）不需要验证，某些任务（如核心算法）可能需要更严格的多轮交叉验证，把决策权交给 Agent 反而更合理。

### 7.2 框架与执行层的契约

框架对自验证只提出三条契约。**契约一**：开发 Agent **被建议**在 `complete-task` 之前自行运行验证；如果决定不验证，应在 `log-step` 中说明理由（例如"本任务为文档变更，无代码产出"）。**契约二**：每次验证迭代的关键事件（开始时间、执行命令、退出码、stdout/stderr 摘要）应通过 `log-step --type verify --step lint --status pass` 上报到 Session 事件流，看板据此渲染验证轨迹。**契约三**：验证循环本身的 LLM 调用同样需要 `consume-budget`，避免成为绕过预算限制的灰色路径。

### 7.3 推荐实现模式（仅供参考，不强制）

为方便执行层快速接入，stage-bridge 的 SKILL.md 中给出一个**参考实现模式**，开发者可以照抄、改造或完全忽略。模式的核心循环用伪代码描述如下：

```
for iteration in range(max_iterations):
  check_control()                                # 必检 ABORT
  code = llm.generate(prompt)
  log_step(type="generate", iteration=iteration)

  for step in self.verify_steps:                 # 由 Agent 自定义的步骤列表
    result = subprocess.run(step.command)
    log_step(type="verify", step=step.name,
             status="pass" if result.ok else "fail")
    if not result.ok and step.must_pass:
      prompt = build_fix_prompt(code, result.stderr)
      break                                      # 跳回 LLM 修订
  else:
    return write_artifact(code) and complete_task()

fail_task(reason="verify_max_iterations_exceeded")
```

`self.verify_steps` 由开发 Agent 在内部硬编码或从代码仓自定义文件（例如 `Makefile`、`package.json` 或自创的 `.agent/verify.local.yaml`）解析得到，**框架完全不感知该文件的存在**。Agent 之间可以共享这个解析逻辑，但通过执行层 SDK 共享，与框架层无关。

### 7.4 与 feedback 链路的区别

执行层自验证与现有的测试 Agent feedback 链路是两层不同粒度的质量保障。自验证聚焦**单服务代码质量**（语法、类型、单元测试），在代码提交前完成；feedback 链路聚焦**跨服务集成质量**（API 契约、E2E 用例），在所有服务部署后由测试 Agent 发起。两者互补：自验证拦截 60-70% 的低级错误，feedback 兜住集成问题。框架对前者无介入，对后者通过 `feedback-write/listen/resolve` 三件套提供基础能力。

---

## 第八章　P1-1　DAG 扩展（Parallelization / Aggregate）

### 8.1 扩展动机

Anthropic 文章将 Workflow 模式细分为五种：Prompt Chaining、Routing、Parallelization、Orchestrator-Workers、Evaluator-Optimizer[1]。当前 v3 的 DAG 只支持最简单的 **Prompt Chaining**（A 完成→B 开始），无法表达"多服务并行开发后聚合测试"等常见研发场景。本章引入两种新节点类型——**Parallel** 与 **Aggregate**——让 DAG 描述能力与真实研发流程对齐。

需要明确的是：**本平台不引入 Routing 节点**。`dependencies.json` 在需求设计阶段由设计 Agent 输出，所有任务和服务归属在那一刻就完全确定，不存在"根据运行时数据走不同分支"的需求。如果未来出现需求形态多变的场景，可以由设计 Agent 在不同输入下输出不同的 `dependencies.json`，把"分支"决策上移到设计阶段，而非在运行期做。

### 8.2 两种新节点语义

**Parallel 节点**声明一组可并行执行的子任务。Aggregator 在 Parallel 节点的 `depends_on` 全部完成后，将其 `children` 列表中所有子任务的 `phase` 置为 `PENDING`，由各自服务的 Agent 主动抢占执行；子任务彼此之间不互相等待。Parallel 节点本身不分配 Agent，phase 在所有 children 完成后由 Aggregator 自动推进到 `DONE`。

**Aggregate 节点**是 fan-in 汇聚点，本身也不分配 Agent 执行。Aggregator 在 Aggregate 节点的 `depends_on`（通常是上游 Parallel 节点）的 phase 变为 `DONE` 后，将 Aggregate 节点自身直接标记为 `DONE` 并推进其下游。Aggregate 节点的存在主要是让 DAG 视觉上清晰、便于看板渲染汇聚关系，逻辑上可以省略——直接让下游任务的 `depends_on` 指向 Parallel 节点也能工作。

### 8.3 dependencies.json 扩展 Schema

扩展后的 `dependencies.json` 示例如下。`type` 字段明确节点种类，默认为 `task`（保持与现有 JSON 兼容）。

```json
{
  "req_id": "req-042",
  "title": "新增用户登录流程",
  "guardrails": { "max_llm_calls": 800, "max_runtime_minutes": 90 },
  "tasks": [
    { "name": "design", "type": "task", "service_name": "_design", "capability": "design" },
    { "name": "design-review", "type": "task", "service_name": "_design", "capability": "review", "depends_on": ["design"] },

    { "name": "parallel-dev", "type": "parallel",
      "depends_on": ["design-review"],
      "children": ["dev-frontend", "dev-backend", "dev-mobile"]
    },

    { "name": "dev-frontend", "type": "task", "service_name": "web-app",      "capability": "dev" },
    { "name": "dev-backend",  "type": "task", "service_name": "user-service", "capability": "dev" },
    { "name": "dev-mobile",   "type": "task", "service_name": "mobile-app",   "capability": "dev" },

    { "name": "merge", "type": "aggregate", "depends_on": ["parallel-dev"] },

    { "name": "test-e2e",    "type": "task", "service_name": "_test",   "capability": "test",   "depends_on": ["merge"] },
    { "name": "deploy-prod", "type": "task", "service_name": "_deploy", "capability": "deploy", "depends_on": ["test-e2e"] }
  ]
}
```

约定：跨服务通用的角色（设计、评审、测试、部署）使用 `_design`、`_test`、`_deploy` 这类前缀下划线的"虚拟服务名"，由对应的设计 Agent / 测试 Agent / 部署 Agent 在 `register-agent` 时声明绑定。这样做既保持了"任务必有 service_name"的统一约束，也清晰区分了"业务微服务"与"流程角色"。

### 8.4 Aggregator 处理逻辑

对 `task` 节点的处理与现有逻辑保持不变：上游全部 `DONE` 时置为 `PENDING`。对 `parallel` 节点，Aggregator 在其 `depends_on` 全部完成后，将其 `children` 全部置为 `PENDING`，并在 Parallel 节点上维护 `children_done_count` 字段；当 `children_done_count == len(children)` 时将 Parallel 节点自身置为 `DONE`。对 `aggregate` 节点，Aggregator 检测其 `depends_on` 的状态，全部 `DONE` 时直接将 Aggregate 节点置为 `DONE` 并推进其下游。

值得强调的是：**Aggregator 全程不知道哪个 Agent 会执行 children 中的任务**——这完全由各自服务的 Agent 通过 `claim-task --service <self>` 主动认领，与第八章之前描述的认领机制完全一致。Parallel 节点的存在只是改变了"何时把 children 置为 PENDING"，并未引入任何 Agent 选择逻辑。

### 8.5 向后兼容保证

没有 `type` 字段的任务一律视为 `task` 类型，现有所有 v3 的 `dependencies.json` 不做任何修改即可继续工作。新节点类型在看板上有差异化的视觉表达：`parallel` 以带括号的组节点绘制，`aggregate` 以倒梯形绘制，让流程一目了然。

---

## 第九章　业务看板

### 9.1 技术形态

业务看板使用 React 19 + Tailwind 4 + shadcn/ui 实现，采用深色科技感主题，适配 PC 与移动端。看板通过直连 Consul HTTP API（读）和直连框架 WebAPI（写）获取数据，不引入独立后端，便于部署。生产环境由 Nginx 做反向代理，隐藏 Consul ACL Token，同时做只读路径和写入路径的权限分离。

### 9.2 核心视图

看板包含四个核心视图。**需求列表**展示所有在途需求，带 Phase 进度条、预算使用率、告警计数。**DAG 拓扑图**展示单个需求的 DAG，支持新引入的 `parallel`、`aggregate` 两种节点的差异化渲染，节点颜色表达状态。**任务详情抽屉**展示单任务的 Session 事件流（含 verify 上报、ABORT 退出记录、预算扣减轨迹）、产物链接、心跳记录。**告警面板**展示预算超限、收敛停滞、重试上限等事件，提供 PAUSE / RESUME / ABORT / RETRY 一键操作（带二次确认）。

### 9.3 实时性

看板对 Consul API 使用 **Blocking Query 长轮询**（`?wait=30s&index=<X-Consul-Index>`），状态变更秒级推送。对高频变更的路径（如 Session 事件流）做合并刷新，避免过度重渲染。所有控制信号写入都带二次确认对话框，避免误操作；ABORT 操作额外要求填写文字理由（写入 `control.reason` 字段），确保操作可追溯。

---

## 参考文献

[1] Anthropic Engineering. *Building Effective Agents*. https://www.anthropic.com/engineering/building-effective-agents

[2] Claude Blog. *Multi-agent Coordination Patterns: Five Approaches and When to Use Them*. https://claude.com/blog/multi-agent-coordination-patterns

[3] iceyao. *多智能体协调模式：五种方法及其使用场景*. https://www.iceyao.com.cn/post/2026-04-14-multi-agent-coordination-patterns/
