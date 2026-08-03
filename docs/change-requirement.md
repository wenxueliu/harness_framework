# 开发中修改需求

同一个业务需求继续复用原 `req_id` 和 workflow。需求内容发布为新版本；如果指定
了需要重做的任务，Harness 归档这些任务及其下游的旧产物和 attempt，随后在原
workflow 下创建 successor run。

```text
req-001
├── requirement v1 → run-001 (SUPERSEDED)
└── requirement v2 → run-002 (RUNNING)
```

## 修改并重跑受影响部分

准备完整的新需求文本，然后执行：

```bash
python scripts/change_requirement.py req-001 \
  --content-file requirement-v2.md \
  --reason "调整账号锁定规则" \
  --redo implement-account-lock \
  --actor alice
```

`--redo` 只填写需求直接改变的任务。Harness 自动计算并重置其所有下游任务。例如：

```text
design                  DONE       保留
implement-account-lock  PENDING    重做
unit-test               BLOCKED    下游重做
e2e-test                BLOCKED    下游重做
unrelated-docs          DONE       保留
```

受影响且正在运行的 task attempt 会被归档并清除；旧 Worker 的迟到结果会被 attempt
fencing 拒绝。不受影响的 DONE 或 IN_PROGRESS 任务保持原状态。

## 只修改文字

不传 `--redo`：

```bash
python scripts/change_requirement.py req-001 \
  --content-file requirement-v2.md \
  --reason "补充文字说明" \
  --actor alice
```

这会发布需求新版本并保留当前 run 和所有任务状态。

## 查询记录

当前需求版本：

```text
workflows/<req_id>/versions/requirement/current
```

简化兼容字段：

```text
workflows/<req_id>/requirement
workflows/<req_id>/requirement_version
```

每次变更记录：

```text
workflows/<req_id>/requirement_changes/<change_id>/record
```

记录包含修改原因、操作者、新旧版本、直接变更任务、完整影响范围以及新旧 run ID。

如果需求已经变成可以独立开发和验收的另一件事，应创建新的 `req_id`，而不是使用
本命令。
