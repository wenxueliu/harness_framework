#!/usr/bin/env python3
"""
FileStore KV CLI — Agent 本地文件存储命令行工具

纯本地模式（--local-file）下，Agent 通过此脚本直接读写 FileStore
的 JSON 文件，无需 Consul 或 HTTP 服务器。

用法：
  python scripts/file_kv.py get <key> [--recurse] [--data-file <path>]
  python scripts/file_kv.py put <key> <value> [--cas <index>] [--data-file <path>]
  python scripts/file_kv.py delete <key> [--recurse] [--data-file <path>]
  python scripts/file_kv.py blocking-get <key> [--index <n>] [--wait <s>] [--data-file <path>]
  python scripts/file_kv.py register '<json>' [--data-file <path>]
  python scripts/file_kv.py deregister <agent-id> [--data-file <path>]
  python scripts/file_kv.py heartbeat <agent-id> [--data-file <path>]
  python scripts/file_kv.py list-services [--data-file <path>]
  python scripts/file_kv.py status-leader [--data-file <path>]

环境变量：
  FILE_STORE_DATA  数据文件路径（优先级低于 --data-file）
"""
import os
import sys

# 确保可以 import harness_framework
_here = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(_here)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

from harness_framework.file_store import file_kv_cli

if __name__ == "__main__":
    file_kv_cli()
