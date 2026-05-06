#!/usr/bin/env python3
"""
native_log_to_sessions.py — Agent 原生日志 → Consul KV Session 事件转换工具

读取 Agent 平台（Claude Code / Codex / OpenCode）的原生日志文件，
解析为结构化 session event，写入 Consul KV。

支持的日志格式:
  - claude-code: Claude Code JSONL 对话文件
  - codex:       Codex JSON 日志
  - opencode:    OpenCode 日志
  - auto:        自动检测（默认）

用法:
  native_log_to_sessions.py <req_id> <task_name> <session_id> <log_file>
      [--format auto|claude-code|codex|opencode] [--dedup] [--start-line N] [--end-line N]

环境变量:
  AGENT_ID      全局唯一 Agent ID（必填）
  CONSUL_ADDR   Consul 地址（默认 127.0.0.1:8500）
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import (  # noqa: E402
    env, kv_get, kv_put, emit_json, die, now_iso,
    ensure_run, session_base, get_current_run,
)


# ── 日志格式检测 ────────────────────────────────────────────────────────────────

def detect_format(filepath: str) -> str:
    """读取文件前几行自动检测日志格式。"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            head = "".join(f.readline() for _ in range(10))
    except (IOError, OSError) as e:
        die(f"无法读取日志文件: {e}", code=2)

    # Claude Code JSONL: 每行以 {"type": 开头
    if re.search(r'^\{"type"\s*:', head, re.MULTILINE):
        return "claude-code"

    # Codex JSON: 包含 "codex" 或 ".codex" 路径
    if "codex" in filepath.lower() or '"tool"' in head:
        return "codex"

    # OpenCode: 包含 "opencode" 或特定标记
    if "opencode" in filepath.lower() or '"opencode"' in head.lower():
        return "opencode"

    # 默认尝试 Claude Code 格式
    return "claude-code"


# ── 解析器 ──────────────────────────────────────────────────────────────────────

def parse_claude_code_line(line: str, line_num: int) -> list[dict]:
    """解析单行 Claude Code JSONL 日志，返回事件列表。"""
    line = line.strip()
    if not line:
        return []

    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return []

    msg_type = record.get("type", "")
    events = []

    if msg_type == "assistant":
        # assistant 消息包含 tool_use blocks
        for block in record.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                tool_name = block.get("name", "unknown")
                tool_input = block.get("input", {})
                events.append({
                    "level": "info",
                    "message": f"[Tool: {tool_name}]",
                    "step_type": _classify_tool(tool_name),
                    "data": _summarize_input(tool_name, tool_input),
                })
            elif block.get("type") == "text":
                text = block.get("text", "")
                if text.strip():
                    events.append({
                        "level": "debug",
                        "message": _truncate(text, 200),
                        "step_type": "ASSISTANT_MSG",
                    })

    elif msg_type == "user":
        # user 消息通常对应 tool result
        for block in record.get("message", {}).get("content", []):
            if block.get("type") == "tool_result":
                tool_id = block.get("tool_use_id", "")
                is_error = block.get("is_error", False)
                content = block.get("content", "")
                if isinstance(content, list):
                    content = "\n".join(
                        c.get("text", "") if isinstance(c, dict) else str(c)
                        for c in content
                    )
                level = "error" if is_error else "info"
                result_text = _truncate(str(content), 200)
                events.append({
                    "level": level,
                    "message": f"[Result] {result_text}" if result_text else "[Result]",
                    "step_type": "TOOL_ERROR" if is_error else "TOOL_RESULT",
                    "data": {"tool_use_id": tool_id},
                })

    return events


def parse_codex_line(line: str, line_num: int) -> list[dict]:
    """解析单行 Codex 日志。"""
    line = line.strip()
    if not line:
        return []

    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return []

    events = []

    # Codex 日志格式: {"timestamp": ..., "event": ..., "data": ...}
    event_type = record.get("event", record.get("type", ""))
    data = record.get("data", {})

    if event_type == "tool_call":
        tool = data.get("tool", data.get("name", "unknown"))
        events.append({
            "level": "info",
            "message": f"[Tool: {tool}]",
            "step_type": _classify_tool(tool),
            "data": _summarize_input(tool, data.get("input", data.get("args", {}))),
        })
    elif event_type == "tool_result":
        is_error = data.get("is_error", False)
        content = data.get("output", data.get("result", ""))
        events.append({
            "level": "error" if is_error else "info",
            "message": f"[Result] {_truncate(str(content), 200)}",
            "step_type": "TOOL_ERROR" if is_error else "TOOL_RESULT",
        })
    elif event_type in ("assistant_message", "message"):
        text = data.get("text", data.get("content", ""))
        if text:
            events.append({
                "level": "debug",
                "message": _truncate(str(text), 200),
                "step_type": "ASSISTANT_MSG",
            })

    return events


def parse_opencode_line(line: str, line_num: int) -> list[dict]:
    """解析单行 OpenCode 日志。"""
    return parse_codex_line(line, line_num)  # 格式与 Codex 类似


# ── 辅助函数 ────────────────────────────────────────────────────────────────────

def _classify_tool(tool_name: str) -> str:
    """将工具名映射到 step_type。"""
    mapping = {
        "read": "FILE_READ", "Read": "FILE_READ",
        "write": "FILE_EDIT", "Write": "FILE_EDIT",
        "edit": "FILE_EDIT", "Edit": "FILE_EDIT",
        "bash": "BASH", "Bash": "BASH",
        "glob": "FILE_READ", "Glob": "FILE_READ",
        "grep": "FILE_READ", "Grep": "FILE_READ",
        "web_search": "WEB", "WebSearch": "WEB",
        "web_fetch": "WEB", "WebFetch": "WEB",
    }
    return mapping.get(tool_name, "TOOL_CALL")


def _summarize_input(tool_name: str, tool_input: dict) -> dict:
    """提取工具输入的摘要信息。"""
    if not isinstance(tool_input, dict):
        return {"args": str(tool_input)[:200]}

    summary = {}
    if "file_path" in tool_input:
        summary["file"] = tool_input["file_path"]
    if "pattern" in tool_input:
        summary["pattern"] = str(tool_input["pattern"])[:100]
    if "command" in tool_input:
        summary["command"] = str(tool_input["command"])[:100]
    if "message" in tool_input or "query" in tool_input:
        pass  # 太长的文本不放入 data

    return summary


def _truncate(text: str, max_len: int) -> str:
    """截断文本到指定长度。"""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


# ── 写入逻辑 ────────────────────────────────────────────────────────────────────

def get_existing_seqs(req_id: str, task_name: str, session_id: str) -> set[str]:
    """获取已存在的 seq 集合（用于去重）。"""
    base = session_base(req_id, task_name, session_id)
    items, _ = kv_get(f"{base}/events", recurse=True)
    if not items:
        return set()

    seqs = set()
    prefix = f"{base}/events/"
    for it in items:
        rel = it["Key"][len(prefix):] if it["Key"].startswith(prefix) else it["Key"]
        seqs.add(rel)
    return seqs


def write_events(req_id: str, task_name: str, session_id: str,
                 agent_id: str, run_id: str,
                 events: list[dict], dedup: bool = False) -> int:
    """将事件列表写入 Consul KV。返回写入的事件数。"""
    base = session_base(req_id, task_name, session_id)
    existing = get_existing_seqs(req_id, task_name, session_id) if dedup else set()
    written = 0

    for event in events:
        ts = now_iso()
        seq = f"{int(time.time() * 1000000)}_{written:06d}"
        # 微秒精度 + 序号防止同一微秒内碰撞

        if dedup:
            # 检查是否有内容完全相同的已存在事件
            is_dup = False
            for ex_seq in existing:
                existing_event, _ = kv_get(f"{base}/events/{ex_seq}")
                if existing_event and event.get("message") in str(existing_event):
                    is_dup = True
                    break
            if is_dup:
                continue

        payload = {
            "ts": ts,
            "agent_id": agent_id,
            "level": event.get("level", "info"),
            "message": event.get("message", ""),
            "step_type": event.get("step_type", "TOOL_CALL"),
            "run_id": run_id,
        }
        if event.get("data"):
            payload["data"] = event["data"]

        kv_put(f"{base}/events/{seq}", json.dumps(payload, ensure_ascii=False))
        kv_put(f"{base}/latest_event", json.dumps(payload, ensure_ascii=False))
        written += 1

        time.sleep(0.0001)  # 确保 seq 递增

    return written


# ── 主入口 ──────────────────────────────────────────────────────────────────────

PARSERS = {
    "claude-code": parse_claude_code_line,
    "codex": parse_codex_line,
    "opencode": parse_opencode_line,
}


def main():
    p = argparse.ArgumentParser(
        description="Agent 原生日志 → Consul Session 事件转换工具"
    )
    p.add_argument("req_id")
    p.add_argument("task_name")
    p.add_argument("session_id")
    p.add_argument("log_file")
    p.add_argument("--format", default="auto",
                   choices=("auto", "claude-code", "codex", "opencode"))
    p.add_argument("--dedup", action="store_true",
                   help="跳过与已有事件内容重复的事件")
    p.add_argument("--start-line", type=int, default=1,
                   help="从第 N 行开始解析（1-indexed）")
    p.add_argument("--end-line", type=int, default=0,
                   help="解析到第 N 行为止（0=到文件末尾）")
    args = p.parse_args()

    agent_id = env("AGENT_ID", required=True)

    # 检测格式
    fmt = args.format
    if fmt == "auto":
        fmt = detect_format(args.log_file)
        print(f"[native_log_to_sessions] 检测到日志格式: {fmt}", file=sys.stderr)

    if fmt not in PARSERS:
        die(f"不支持的日志格式: {fmt}，可选: {', '.join(PARSERS)}", code=1)

    parse_fn = PARSERS[fmt]

    # 确保 run 存在
    run_id = get_current_run(args.req_id) or ensure_run(args.req_id)

    # 逐行解析
    total_events = 0
    parsed_lines = 0
    error_lines = 0

    try:
        with open(args.log_file, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                if line_num < args.start_line:
                    continue
                if args.end_line > 0 and line_num > args.end_line:
                    break

                try:
                    events = parse_fn(line, line_num)
                    if events:
                        n = write_events(
                            args.req_id, args.task_name, args.session_id,
                            agent_id, run_id,
                            events, dedup=args.dedup,
                        )
                        total_events += n
                        parsed_lines += 1
                except Exception as e:
                    error_lines += 1
                    if error_lines <= 3:
                        print(f"[warn] 第 {line_num} 行解析失败: {e}",
                              file=sys.stderr)
    except (IOError, OSError) as e:
        die(f"读取日志文件失败: {e}", code=2)

    emit_json({
        "ok": True,
        "format": fmt,
        "total_events": total_events,
        "parsed_lines": parsed_lines,
        "error_lines": error_lines,
        "session_id": args.session_id,
        "run_id": run_id,
        "task_name": args.task_name,
    })


if __name__ == "__main__":
    main()
