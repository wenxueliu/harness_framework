#!/usr/bin/env bash
# stop_all.sh — 停止 Harness Framework 全套服务

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="${XDG_RUNTIME_DIR:-/tmp}/harness-framework"
PID_DIR="$LOG_DIR/pids"

echo "停止所有服务..."

# 读取 PID 文件并停止
for pid_file in "$PID_DIR"/*.pid 2>/dev/null; do
    if [[ -f "$pid_file" ]]; then
        name=$(basename "$pid_file" .pid)
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null && echo "已停止 $name (PID: $pid)" || true
        fi
        rm -f "$pid_file"
    fi
done

# 也停止可能的遗留进程
pkill -f "harness_framework.daemon" 2>/dev/null && echo "已停止 harness_framework" || true
pkill -f "vite" 2>/dev/null && echo "已停止 agent_dashboard" || true
pkill -f "consul agent -dev" 2>/dev/null && echo "已停止 Consul" || true

echo "完成"
