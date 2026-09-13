# Dashboard 不可逆边界 ADR 汇总

日期：2026-09-13

## 1. Workspace-first Run 与旧 CLI

Dashboard 创建的生产 Run 必须先创建 `PROVISIONING` Run Workspace，准备成功后才进入 `RUNNING`。旧 `--publish` 继续兼容，但只在非 managed 模式使用受控 `acp.cwd`/`repo_path`；managed 模式缺少显式 Workspace 时拒绝启动。

## 2. KVStore 一致性

主记录使用 CAS 创建和更新，列表索引可重建；Local、File、Consul 统一使用 `kv_list` 和不透明游标。索引不一致不能创建第二份事实记录，修复通过后台运维任务处理。

## 3. 可信代理认证

只有来自配置的代理地址才可以提供身份头；local 模式使用 daemon 配置的单一明确身份。请求正文中的 actor 永远不能参与授权或审计身份。

## 4. EventJournal 与 SSE

事件先写入 KV 再对客户端可见，按 stream sequence 保留有限窗口；Last-Event-ID 过期返回 `STREAM_RESET_REQUIRED`。标准库入口保持兼容，ASGI 入口用于生产长连接和慢客户端隔离。

## 5. Workspace 隔离等级

`ORIGINAL`、`GIT_WORKTREE`、`CONTROLLED_COPY`、`DEMO_TEMP` 是显式策略。Attempt Binding 不可变；隔离 Attempt 的修改只能通过显式 Merge Task 经三方 Git apply 合并，不能由调度器隐式覆盖共享目录。
