#!/usr/bin/env python3
"""
executor_placeholder.py — 占位 executor，用于测试 worker 循环。

在 step 3 中，这里会替换为实际的 TDD 工作流 executor。
读取 worker 传入的 JSON，模拟执行，返回结果。

用法:
  echo '{"req_id":"...","task_name":"...","task_meta":{...}}' | python3 executor_placeholder.py
"""

import json
import sys
import time


def main():
    try:
        task_input = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "FAILED", "error": f"JSON 解析失败: {e}"}))
        sys.exit(1)

    req_id = task_input.get("req_id", "?")
    task_name = task_input.get("task_name", "?")
    task_type = task_input.get("task_meta", {}).get("type", "backend")
    service = task_input.get("task_meta", {}).get("service_name", "?")

    print(f"[executor:placeholder] 开始: {req_id}/{task_name} "
          f"(type={task_type}, service={service})", file=sys.stderr)

    # 模拟执行
    time.sleep(1)

    print(f"[executor:placeholder] 完成: {req_id}/{task_name}", file=sys.stderr)

    result = {
        "status": "DONE",
        "mode": "placeholder",
        "req_id": req_id,
        "task_name": task_name,
        "summary": f"模拟执行成功: {task_name}",
        "artifacts": {
            "branch": f"hw-{task_name}",
            "files_changed": 0,
        },
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
