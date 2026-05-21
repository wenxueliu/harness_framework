---
name: doc-update
description: |
  Review and update documentation to follow the progressive-disclosure pattern.
  Reads existing docs, checks for gaps/overlaps/broken references, and applies fixes:
  add navigation headers at the top, fix cross-references, trim duplicate content,
  ensure "next steps" at the bottom, position each doc at the right learning level.
  Use when: docs feel unorganized, new feature needs docs, or a routine doc review.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Edit
  - Write
---

# Doc Update — 文档维护 Skill

根据 [README.md](../../README.md) 和 `docs/` 目录下的文档结构，维护一套遵循**渐进式披露**原则的文档体系。

## 文档结构总览

```
README.md                           ← Level 0: 入口页，一句话说明 + 一条命令 + 导航表
├── docs/quickstart.md              ← Level 1: 1 分钟跑起来（3 条命令）
├── docs/getting-started.md         ← Level 2: 定义第一个 DAG 工作流
├── docs/concepts.md                ← Level 3: 核心概念（状态机、组件、协作模型）
├── docs/architecture.md            ← Level 4: 完整架构设计文档
├── docs/configuration.md           ← CLI 参数和环境变量速查表
├── docs/agent-guide.md             ← Agent 接入指南（单机 / HTTP / Skill）
├── docs/usage-guide.md             ← 操作参考手册 + FAQ
├── docs/storage-modes.md           ← 三种存储后端深度对比
├── docs/message-bus.md             ← 任务间 Message Bus
├── docs/proposal-protocol.md       ← 动态任务提案协议
├── docs/status-state-machine.md    ← 状态机完整定义
├── docs/dynamic-tasks.md           ← 动态任务设计
├── docs/agent-retry-pattern.md     ← 失败处理与重试机制
└── docs/memory-model.md            ← Agent 记忆模型说明
```

## 文档设计原则

### 原则 1：渐进式披露

文档分 Level，读者从 Level 0 一路读到 Level 4：

```
README(0) → quickstart(1) → getting-started(2) → concepts(3) → deep-dives(4)
                              ↑
                         agent-guide 也可从此进入
```

- 不要出现 Level 1 的文档引用 Level 4 的文档
- 每篇文档只假设读者读了所有更低 Level 的文档

### 原则 2：顶部导航锚点

**每篇**深度文档（Level 2-4，含所有 `docs/` 下的独立话题）顶部必须有一行提示：

```markdown
> **初次接触？** 先看 [quickstart.md](quickstart.md) 和 [concepts.md](concepts.md)。本文是 YYY 的完整说明。
```

具体指向规则：

| 目标文档 | 导航指向 | 例子 |
|---------|---------|------|
| 独立的深度文档（message-bus、proposal-protocol 等） | `quickstart.md` + `concepts.md` | `> **初次接触？** 先看 [quickstart.md](quickstart.md) 和 [concepts.md](concepts.md)。本文是消息通信的完整说明。` |
| architecture.md | `quickstart.md` + `concepts.md` | `> **初次接触？** 从 [quickstart.md](quickstart.md) 开始，了解核心概念看 [concepts.md](concepts.md)。本文是完整的架构设计文档。` |
| usage-guide.md | `concepts.md` | `> 概念解释见 [concepts.md](concepts.md)。` |
| 入口页（README.md） | 不设此导航（本身就是起点） | — |

### 原则 3：底部"下一步"导航

Level 1-3 的文档末尾必须包含"下一步"表格，指引读者往哪里走：

```markdown
## 下一步

| 我想… | 看这里 |
|-------|--------|
| 深入理解核心概念 | [concepts.md →](concepts.md) |
| 把 Agent 接入框架 | [agent-guide.md →](agent-guide.md) |
```

### 原则 4：不重复内容

同一个概念只在一处解释为**权威出处**，其他文档引用而非复制：

| 概念 | 权威出处 | 其他文档做法 |
|------|---------|------------|
| 任务类型和状态流转 | `concepts.md`（概述）、`status-state-machine.md`（完整定义） | `详见 [concepts.md](concepts.md)` |
| CLI 参数表 | `configuration.md` | 引用 `configuration.md` |
| Agent 操作命令 | `agent-guide.md` | 引用 `agent-guide.md` |
| 架构总览 | `architecture.md`（完整）、`concepts.md`（简化） | 按需选择引用 |

### 原则 5：代码示例完整可执行

- 每条命令都假设读者从项目根目录（`harness_framework/`）执行
- 不省略路径前缀（如 `python scripts/sync_to_consul.py ...` 而非 `sync_to_consul.py ...`）
- 必须包含 `--local` 或 `--local-file` 的示例（新手最常用的模式）

### 原则 6：中文优先

默认使用中文编写。需要双语时用 `[English](#english) | [中文](#中文)` 切换。

## 文档健康检查

### 1. 定位检查

- [x] 这篇文档适合什么阶段的读者（Level 0-4）？
- [x] 是否假设了读者已读过低 Level 的文档？
- [x] 顶部有导航指向更低 Level 的文档吗？
- [x] Level 1-3 的文档底部有"下一步"表格吗？

### 2. 内容检查

- [x] 有没有与其他文档重复的关键内容？（应引用而非复制）
- [x] 有没有过时的信息（旧 API 路径、旧参数名）？
- [x] 命令示例是否包含 `--local` / `--local-file` / `--standalone`？
- [x] 配置项是否同步更新了 `configuration.md`？

### 3. 链接检查

- [x] 内部链接全部使用**相对路径**（`xxx.md` 或 `docs/xxx.md`）
- [x] 没有断链（`grep` 确认目标文件存在）
- [x] 没有引用过期文件名（如 `archtecture.md`、`storage-modes-and-e2e-flow.md`）

```bash
# 检查过期引用的命令
grep -rn "archtecture\\.md\\|storage-modes-and-e2e-flow\\.md" docs/
```

### 4. 入口页检查（README.md）

- [x] 是否一句话说明了"这是什么"？
- [x] 是否包含一行命令让读者尝试？
- [x] 是否包含文档导航表？
- [x] 是否没有过深的架构/配置/安装内容？

## 常见修复模式

### 修复 1：添加导航头

在文件顶部（标题下方）插入：

```markdown
> **初次接触？** 先看 [quickstart.md](quickstart.md) 和 [concepts.md](concepts.md)。本文是 YYY 的完整说明。
```

添加前确认该文档尚未有此类导航。

### 修复 2：添加"下一步"表格

在 Level 1-3 文档末尾插入：

```markdown
## 下一步

| 我想… | 看这里 |
|-------|--------|
| 深入理解核心概念 | [concepts.md →](concepts.md) |
| ... | ... |
```

表格的行对应文档内容覆盖范围，让读者理解"读完了这篇，还能读什么"。

### 修复 3：删除重复内容（替换为引用）

找到重复内容后：

```
原： 框架使用三种存储后端：Consul（生产）、Local（开发）、Local-File（单机调试）。
     Consul 模式需要先安装 Consul 二进制文件...
改： 三种存储后端的详细对比见 [storage-modes.md](storage-modes.md)。
```

搜索重复内容：

```bash
# 检查各文档是否都有"快速开始"章节
grep -c "快速开始\|快速启动\|快速上手" docs/*.md

# 检查是否有多处"安装 Consul"的描述
grep -c "安装 Consul\|start_consul_dev" docs/*.md
```

### 修复 4：更新 CLAUDE.md 中的命令

CLAUDE.md 中的常用命令和配置表应与 `docs/configuration.md` 同步。

```bash
# 检查 CLAUDE.md 中是否漏了 --standalone 相关命令
grep "standalone\|local-file" CLAUDE.md

# 如果漏了，加上
```

## 典型工作流

### 工作流 1：为新建文档做初始化

从零创建一篇遵循渐进式原则的文档：

1. **确定 Level**：根据内容深度（quickstart=1, getting-started/concepts=2, deep-dives=3, architecture=4）
2. **写内容**
3. **加导航头**：指向 Level-1 的文档
4. **加"下一步"**：指向 Level+1 的文档
5. **更新 README.md 导航表**：确认入口页包含了新文档的链接
6. **更新相关文档的"下一步"表**：让从其他文档能导航过来

### 工作流 2：存量文档规范化

对已有文档做规范化改造：

1. **运行健康检查**：遍历每个检查项
2. **修复导航头**：顶部加导航锚点
3. **修复"下一步"**：底部加下一步指引
4. **去重**：查找被其他文档覆盖的内容，改为引用
5. **修复过期引用**：替换旧文件名
6. **验证**：测试所有内部链接

### 工作流 3：新功能发布后更新文档

1. 更新 `configuration.md`（如果有新 CLI 参数）
2. 更新 `storage-modes.md`（如果涉及存储模式变化）
3. 更新 `concepts.md`（如果有新概念）
4. 更新 `CLAUDE.md` 中的命令示例

## 常见命令

```bash
# 列出所有文档
ls -la docs/*.md

# 统计文档行数（判断是否需要拆分）
wc -l docs/*.md | sort -n

# 搜索跨文档重复
grep -c "快速开始\|快速入门" docs/*.md

# 搜索过期引用
grep -rn "archtecture\\.md\\|storage-modes-and-e2e-flow\\.md" docs/ CLAUDE.md

# 搜索缺少导航头的文档
for f in docs/*.md; do
  if ! head -5 "$f" | grep -q "初次接触"; then
    echo "MISSING: $f"
  fi
done
```

## 与相关 skill 的关系

| Skill | 关系 |
|-------|------|
| `add-task` | 文档中引用 add_task.py 命令时，指向其 SKILL.md |
| `file-kv` | `--local-file` 模式的 Agent 操作引用 file-kv SKILL |
| `stage-bridge` | Agent 生命周期管理引用 stage-bridge SKILL |
