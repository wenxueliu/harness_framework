#!/bin/bash
# E2E 测试一键运行脚本
#
# 参考 gstack 的环境检查模式 + AutoCLI 的 CI 集成思路：
# 1. 检查环境（Consul, daemon, dashboard）
# 2. 确认 Kimi WebBridge daemon 可用
# 3. 启动服务 → 运行测试 → 输出报告 → 清理
#
# 用法:
#   ./scripts/run_e2e.sh              # 完整 E2E 测试
#   ./scripts/run_e2e.sh --smoke      # 仅冒烟测试
#   ./scripts/run_e2e.sh --visual     # 仅视觉测试
# 浏览器始终是用户当前的真实 Chrome，无 headless/headed 模式切换。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
E2E_DIR="$PROJECT_DIR/tests/e2e"
REPORT_DIR="$E2E_DIR/reports"
BASELINE_DIR="$E2E_DIR/baselines/screenshots"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[E2E]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[E2E]${NC} $*"; }
log_error() { echo -e "${RED}[E2E]${NC} $*"; }

# ---- 参数解析 ----
PYTEST_ARGS=""
SKIP_ENV_CHECK=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --smoke)
            PYTEST_ARGS="$PYTEST_ARGS -m smoke"
            shift ;;
        --visual)
            PYTEST_ARGS="$PYTEST_ARGS -m visual"
            shift ;;
        --performance|--perf)
            PYTEST_ARGS="$PYTEST_ARGS -m performance"
            shift ;;
        --a11y)
            PYTEST_ARGS="$PYTEST_ARGS -m a11y"
            shift ;;
        --headed)
            log_warn "--headed 已废弃：Kimi WebBridge 始终使用真实浏览器"
            shift ;;
        --update-snapshots)
            PYTEST_ARGS="$PYTEST_ARGS --update-snapshots"
            shift ;;
        --skip-env-check)
            SKIP_ENV_CHECK="true"
            shift ;;
        *)
            PYTEST_ARGS="$PYTEST_ARGS $1"
            shift ;;
    esac
done

# ---- 目录准备 ----
mkdir -p "$REPORT_DIR" "$BASELINE_DIR"

# ---- 环境检查 ----
if [ "$SKIP_ENV_CHECK" != "true" ]; then
    log_info "检查环境..."

    # 检查 Consul
    if curl -s --connect-timeout 2 http://127.0.0.1:8500/v1/status/leader > /dev/null 2>&1; then
        log_info "Consul: OK (127.0.0.1:8500)"
    else
        log_warn "Consul: 不可达 — 看板将使用 mock 模式"
    fi

    # 检查 Dashboard dev server
    if curl -s --connect-timeout 2 http://localhost:3000 > /dev/null 2>&1; then
        log_info "Dashboard: OK (localhost:3000)"
    else
        log_warn "Dashboard: 不可达 — 尝试启动..."
        if [ -f "$PROJECT_DIR/agent_dashboard/package.json" ]; then
            cd "$PROJECT_DIR/agent_dashboard"
            npm run dev &
            DASHBOARD_PID=$!
            log_info "Dashboard PID: $DASHBOARD_PID"

            # 等待启动
            for i in $(seq 1 30); do
                if curl -s --connect-timeout 1 http://localhost:3000 > /dev/null 2>&1; then
                    log_info "Dashboard: 启动完成"
                    break
                fi
                sleep 1
            done
        fi
    fi
fi

export E2E_BASE_URL="${E2E_BASE_URL:-http://localhost:3000}"
log_info "BASE_URL: $E2E_BASE_URL"

# ---- 浏览器桥接检查 ----
log_info "检查 Kimi WebBridge..."
if ! curl -s --connect-timeout 2 http://127.0.0.1:10086 > /dev/null 2>&1; then
    log_info "启动 Kimi WebBridge daemon..."
    "$HOME/.kimi-webbridge/bin/kimi-webbridge" start
fi
if ! curl -s --connect-timeout 2 http://127.0.0.1:10086 > /dev/null 2>&1; then
    log_error "Kimi WebBridge 不可用，请检查浏览器扩展连接"
    exit 2
fi
log_info "Kimi WebBridge: OK"

# ---- 运行测试 ----
log_info "运行 E2E 测试..."
cd "$PROJECT_DIR"

# 生成带时间戳的报告文件
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
JUNIT_REPORT="$REPORT_DIR/e2e-report-$TIMESTAMP.xml"
HTML_REPORT="$REPORT_DIR/e2e-report-$TIMESTAMP.html"

# 使用 pytest 运行（如果安装了 pytest-html 和 pytest-xdist）
python -m pytest tests/e2e/ \
    $PYTEST_ARGS \
    --junitxml="$JUNIT_REPORT" \
    --timeout=60 \
    2>&1 | tee "$REPORT_DIR/e2e-output-$TIMESTAMP.log"

EXIT_CODE=${PIPESTATUS[0]}

# ---- 结果输出 ----
echo ""
if [ $EXIT_CODE -eq 0 ]; then
    log_info "所有 E2E 测试通过"
else
    log_error "E2E 测试失败 (exit code: $EXIT_CODE)"
    log_info "JUnit 报告: $JUNIT_REPORT"
    log_info "失败截图: $REPORT_DIR/FAILED-*.png"
fi

# ---- 清理 ----
if [ -n "${DASHBOARD_PID:-}" ]; then
    log_info "停止 Dashboard (PID: $DASHBOARD_PID)..."
    kill "$DASHBOARD_PID" 2>/dev/null || true
fi

exit $EXIT_CODE
