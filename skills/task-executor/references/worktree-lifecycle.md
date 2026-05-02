# Worktree Lifecycle Management

任务执行期间 worktree 的完整生命周期管理。

## 创建

```bash
cd "$REPO_PATH"
TASK_SLUG=$(echo "$TASK_NAME" | tr '/' '-' | tr '[:upper:]' '[:lower:]')
BRANCH="hw-$TASK_SLUG"
WORKTREE_PATH="${WORKTREE_BASE:-.worktree}/$TASK_SLUG"

git worktree add "$WORKTREE_PATH" -b "$BRANCH"
cd "$WORKTREE_PATH"
```

## 命名规范

| 元素 | 规则 | 示例 |
|------|------|------|
| Branch | `hw-<task_slug>` | `hw-api-契约设计` |
| Path | `{worktree_base}/<task_slug>` | `.worktree/api-契约设计` |
| Task slug | kebab-case, ≤ 64 chars | `implement-user-register-api` |

## 隔离策略

- 每个任务一个 worktree
- 不同需求的 worktree 可共存（不同分支）
- Worktree 之间通过 Consul context 共享信息，不直接通信

## 开发期间的 Git 操作

```bash
# 提交中间工作
git add <files>
git commit -m "wip(<task_slug>): <阶段性描述>"

# 从主分支同步（如需要）
cd "$REPO_PATH"
git merge main -m "sync: merge main into $BRANCH"
```

## 合并

任务完成后合并回主分支:

```bash
cd "$WORKTREE_PATH"
git add -A
git commit -m "feat($TASK_SLUG): <任务描述>

Implements: <req_id>/<task_name>
Generated with [Claude Code](https://claude.ai/code)
via [Happy](https://happy.engineering)

Co-Authored-By: Claude <noreply@anthropic.com>
Co-Authored-By: Happy <yesreply@happy.engineering>"

# 切回主仓库
cd "$REPO_PATH"

# 合并
git merge "$BRANCH" --no-ff -m "merge: $BRANCH → main"
```

## 清理

```bash
cd "$REPO_PATH"

# 移除 worktree
git worktree remove "$WORKTREE_PATH" --force

# 删除分支（先确认已合并）
git branch -d "$BRANCH" 2>/dev/null || git branch -D "$BRANCH"

# 清理 worktree 目录
rm -rf "$WORKTREE_PATH"
```

## 异常处理

### 合并冲突

```
1. git merge "$BRANCH" → CONFLICT
2. 分析冲突原因
3. 手动解决冲突
4. git add + git commit
5. 重跑 UT + API test（确认解决未引入回归）
6. 继续流程
```

### Worktree 残留

如果 worktree 创建失败但分支已存在:
```bash
# 检查
git worktree list
git branch --list "hw-*"

# 清理
git worktree remove "$WORKTREE_PATH" --force 2>/dev/null
git branch -D "$BRANCH" 2>/dev/null
```

### 上一个任务未清理

Worker 启动时检查状态文件 (`skills/stage-bridge/.worker_state.json`):
- 如果有残留的 in-progress 任务 → 先完成或失败它
- 检查 worktree 是否还存在 → 清理
