#!/usr/bin/env bash
# start_consul_dev.sh — 启动单节点 Consul dev mode（仅用于本地 MVP）
#
# 默认监听 127.0.0.1:8500，启用 CORS（默认允许所有 origin）以便看板直连。
# 退出：Ctrl+C。

set -euo pipefail

CONSUL_BIN="${CONSUL_BIN:-consul}"
DATA_DIR="${CONSUL_DATA_DIR:-/tmp/consul-dev}"

if ! command -v "$CONSUL_BIN" >/dev/null 2>&1; then
  echo "未找到 consul 命令，请先安装："
  echo "  Mac:    brew install consul"
  echo "  Linux:  curl -LO https://releases.hashicorp.com/consul/1.18.1/consul_1.18.1_linux_amd64.zip && unzip consul_*.zip && sudo mv consul /usr/local/bin/"
  exit 1
fi

mkdir -p "$DATA_DIR"

# CORS 配置：允许所有来源（仅 MVP 本地开发使用）
CONFIG_FILE="$(mktemp /tmp/consul-cors-XXXX.hcl)"
cat > "$CONFIG_FILE" <<'EOF'
http_config {
  response_headers {
    "Access-Control-Allow-Origin"  = "*"
    "Access-Control-Allow-Methods" = "GET, POST, PUT, DELETE, OPTIONS"
    "Access-Control-Allow-Headers" = "Content-Type, X-Consul-Token, X-Consul-Index"
    "Access-Control-Expose-Headers" = "X-Consul-Index, X-Consul-Knownleader, X-Consul-Lastcontact"
  }
}
ui_config {
  enabled = true
}
EOF

echo "=========================================="
echo "  Consul dev mode 启动中..."
echo "  HTTP API : http://127.0.0.1:8500"
echo "  Consul UI: http://127.0.0.1:8500/ui"
echo "  数据目录 : $DATA_DIR"
echo "  CORS 配置: $CONFIG_FILE"
echo "=========================================="

exec "$CONSUL_BIN" agent -dev \
  -client=0.0.0.0 \
  -data-dir="$DATA_DIR" \
  -config-file="$CONFIG_FILE"
