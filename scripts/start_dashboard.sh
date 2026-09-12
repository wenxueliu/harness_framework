#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DASHBOARD_DIR="$PROJECT_DIR/agent_dashboard"

if ! command -v node >/dev/null 2>&1; then
    echo "Error: Node.js 22.12+ is required." >&2
    exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
    echo "Error: npm is required." >&2
    exit 1
fi

node -e '
const [major, minor] = process.versions.node.split(".").map(Number);
if (major < 22 || (major === 22 && minor < 12)) process.exit(1);
' || {
    echo "Error: Node.js 22.12+ is required (current: $(node --version))." >&2
    exit 1
}

cd "$DASHBOARD_DIR"

INSTALL_STATE="node_modules/.package-lock.json"
if [[ ! -d node_modules || ! -f "$INSTALL_STATE" || package-lock.json -nt "$INSTALL_STATE" ]]; then
    echo "Installing Dashboard dependencies..."
    npm install
fi

echo "Starting Dashboard at http://127.0.0.1:${PORT:-3000}/"
exec npm run dev
