#!/usr/bin/env bash
# ============================================================
# 后端重启脚本（停止 API + Worker，再重新启动）
# 使用方式：./scripts/restart.sh
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"

echo "⏹  正在停止后端服务..."
bash "$BACKEND_DIR/scripts/stop.sh"

echo "▶️  正在重新启动后端服务..."
bash "$BACKEND_DIR/scripts/start.sh"
