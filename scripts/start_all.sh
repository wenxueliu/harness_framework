#!/usr/bin/env bash
# start_all.sh — 一键启动 Harness Framework 全套服务 (Linux/macOS/Git Bash)
#
# 启动顺序：Consul → harness_framework → agent_dashboard
# 退出：Ctrl+C 或运行 stop_all.sh
#
# Windows 用户请使用 PowerShell 版本：
#   .\scripts\start_all.ps1
#
# 使用方式：
#   ./scripts/start_all.sh          # 启动全部服务
#   ./scripts/start_all.sh --consul-only    # 仅启动 Consul
#   ./scripts/start_all.sh --daemon-only    # 仅启动 harness_framework
#   ./scripts/start_all.sh --dashboard-only # 仅启动 agent_dashboard

set -euo pipefail

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONSUL_DIR="$PROJECT_DIR/consul_server"
DASHBOARD_DIR="$PROJECT_DIR/agent_dashboard"
LOG_DIR="${XDG_RUNTIME_DIR:-/tmp}/harness-framework"
PID_DIR="$LOG_DIR/pids"

# 默认配置
CONSUL_PORT="${CONSUL_PORT:-8500}"
DAEMON_PORT="${DAEMON_PORT:-8080}"
DASHBOARD_PORT="${DASHBOARD_PORT:-3000}"

mkdir -p "$LOG_DIR" "$PID_DIR"

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查进程是否运行
is_running() {
    local pid=$1
    kill -0 "$pid" 2>/dev/null
}

# 检查端口是否被占用
is_port_free() {
    local port=$1
    ! lsof -i ":$port" -sTCP:LISTEN -t >/dev/null 2>&1
}

# 启动 Consul
start_consul() {
    if ! is_port_free "$CONSUL_PORT"; then
        log_warn "Consul 已在端口 $CONSUL_PORT 运行"
        return 0
    fi

    log_info "启动 Consul..."

    local config_file="$LOG_DIR/consul-cors.hcl"
    cat > "$config_file" <<'EOF'
http_config {
  response_headers {
    "Access-Control-Allow-Origin"  = "*"
    "Access-Control-Allow-Methods" = "GET, POST, PUT, DELETE, OPTIONS"
    "Access-Control-Allow-Headers" = "Content-Type, X-Consul-Token, X-Consul-Index"
    "Access-Control-Expose-Headers" = "X-Consul-Index, X-Consul-Knownleader, X-Consul-Lastcontact"
  }
}
EOF

    # 优先使用项目内的 consul 二进制，否则使用 PATH 中的
    local consul_bin="$CONSUL_DIR/consul"
    if [[ ! -x "$consul_bin" ]]; then
        consul_bin="consul"
    fi

    nohup "$consul_bin" agent -dev \
        -client=0.0.0.0 \
        -data-dir="$LOG_DIR/consul-data" \
        -config-file="$config_file" \
        > "$LOG_DIR/consul.log" 2>&1 &

    local pid=$!
    echo $pid > "$PID_DIR/consul.pid"

    # 等待启动
    local retries=10
    while ! curl -s "http://127.0.0.1:$CONSUL_PORT/v1/status/leader" >/dev/null 2>&1; do
        sleep 0.5
        ((retries--))
        if [[ $retries -eq 0 ]]; then
            log_error "Consul 启动失败，查看日志: $LOG_DIR/consul.log"
            exit 1
        fi
    done

    log_info "Consul 已启动 (PID: $pid, 端口: $CONSUL_PORT)"
}

# 启动 harness_framework daemon
start_daemon() {
    if ! is_port_free "$DAEMON_PORT"; then
        log_warn "harness_framework 已在端口 $DAEMON_PORT 运行"
        return 0
    fi

    log_info "启动 harness_framework daemon..."

    cd "$PROJECT_DIR"
    nohup python3 -m harness_framework.daemon \
        --port "$DAEMON_PORT" \
        --consul "127.0.0.1:$CONSUL_PORT" \
        > "$LOG_DIR/daemon.log" 2>&1 &

    local pid=$!
    echo $pid > "$PID_DIR/daemon.pid"

    # 等待启动
    local retries=10
    while ! curl -s "http://127.0.0.1:$DAEMON_PORT/api/workflows" >/dev/null 2>&1; do
        sleep 0.5
        ((retries--))
        if [[ $retries -eq 0 ]]; then
            log_error "harness_framework 启动失败，查看日志: $LOG_DIR/daemon.log"
            exit 1
        fi
    done

    log_info "harness_framework 已启动 (PID: $pid, 端口: $DAEMON_PORT)"
}

# 启动 agent_dashboard
start_dashboard() {
    if ! is_port_free "$DASHBOARD_PORT"; then
        log_warn "agent_dashboard 已在端口 $DASHBOARD_PORT 运行"
        return 0
    fi

    log_info "启动 agent_dashboard..."

    cd "$DASHBOARD_DIR"

    # 检查 node_modules
    if [[ ! -d "node_modules" ]]; then
        log_info "安装依赖..."
        npm install --silent
    fi

    PORT="$DASHBOARD_PORT" nohup npm run dev \
        > "$LOG_DIR/dashboard.log" 2>&1 &

    local pid=$!
    echo $pid > "$PID_DIR/dashboard.pid"

    # 等待启动
    local retries=30
    while ! curl -s "http://127.0.0.1:$DASHBOARD_PORT" >/dev/null 2>&1; do
        sleep 1
        ((retries--))
        if [[ $retries -eq 0 ]]; then
            log_error "agent_dashboard 启动失败，查看日志: $LOG_DIR/dashboard.log"
            exit 1
        fi
    done

    log_info "agent_dashboard 已启动 (PID: $pid, 端口: $DASHBOARD_PORT)"
}

# 停止所有服务
stop_all() {
    log_info "停止所有服务..."

    for pid_file in "$PID_DIR"/*.pid; do
        if [[ -f "$pid_file" ]]; then
            local name=$(basename "$pid_file" .pid)
            local pid=$(cat "$pid_file")
            if is_running "$pid"; then
                kill "$pid" 2>/dev/null && log_info "已停止 $name (PID: $pid)" || true
            fi
            rm -f "$pid_file"
        fi
    done

    # 也停止可能的遗留进程
    pkill -f "harness_framework.daemon" 2>/dev/null || true
    pkill -f "vite" 2>/dev/null || true
    pkill -f "consul agent -dev" 2>/dev/null || true

    log_info "所有服务已停止"
}

# 状态检查
status() {
    log_info "服务状态："

    echo -n "  Consul ($CONSUL_PORT): "
    if curl -s "http://127.0.0.1:$CONSUL_PORT/v1/status/leader" >/dev/null 2>&1; then
        echo -e "${GREEN}运行中${NC}"
    else
        echo -e "${RED}未运行${NC}"
    fi

    echo -n "  harness_framework ($DAEMON_PORT): "
    if curl -s "http://127.0.0.1:$DAEMON_PORT/api/workflows" >/dev/null 2>&1; then
        echo -e "${GREEN}运行中${NC}"
    else
        echo -e "${RED}未运行${NC}"
    fi

    echo -n "  agent_dashboard ($DASHBOARD_PORT): "
    if curl -s "http://127.0.0.1:$DASHBOARD_PORT" >/dev/null 2>&1; then
        echo -e "${GREEN}运行中${NC}"
    else
        echo -e "${RED}未运行${NC}"
    fi
}

# 打印访问地址
print_urls() {
    echo ""
    log_info "访问地址："
    echo "  Consul UI:    http://localhost:$CONSUL_PORT/ui"
    echo "  API:          http://localhost:$DAEMON_PORT"
    echo "  Dashboard:    http://localhost:$DASHBOARD_PORT"
    echo ""
}

# 主流程
main() {
    local mode="${1:-all}"

    case "$mode" in
        --consul-only)
            start_consul
            print_urls
            ;;
        --daemon-only)
            start_consul
            start_daemon
            print_urls
            ;;
        --dashboard-only)
            start_dashboard
            print_urls
            ;;
        --stop)
            stop_all
            ;;
        --status)
            status
            ;;
        all|"")
            start_consul
            start_daemon
            start_dashboard
            print_urls
            log_info "按 Ctrl+C 停止所有服务"
            # 等待中断
            trap 'stop_all; exit 0' INT TERM
            tail -f "$LOG_DIR"/*.log 2>/dev/null || sleep infinity
            ;;
        *)
            echo "用法: $0 [--consul-only|--daemon-only|--dashboard-only|--stop|--status]"
            exit 1
            ;;
    esac
}

main "$@"
