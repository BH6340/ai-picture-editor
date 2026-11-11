#!/usr/bin/env bash
# ============================================================
# 后端停止脚本（停止 API + Worker）
# 使用方式：./scripts/stop.sh
# ============================================================

set -e

# 脚本所在目录（backend/）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
PID_DIR="$BACKEND_DIR/logs"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}  停止后端服务（API + Worker）${NC}"
echo -e "${YELLOW}========================================${NC}"
echo ""

cd "$BACKEND_DIR"

# 优雅停止函数
stop_process() {
    local name="$1"
    local pid_file="$2"
    local timeout="${3:-10}"

    if [ ! -f "$pid_file" ]; then
        echo -e "${YELLOW}⚠️  ${name} 的 PID 文件不存在，跳过${NC}"
        return 0
    fi

    local pid
    pid=$(cat "$pid_file")

    if ! kill -0 "$pid" 2>/dev/null; then
        echo -e "${YELLOW}⚠️  ${name} 进程（PID: $pid）已不存在，清理 PID 文件${NC}"
        rm -f "$pid_file"
        return 0
    fi

    echo "⏹  停止 ${name}（PID: $pid）..."
    kill -TERM "$pid"

    # 等待进程退出
    local waited=0
    while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt "$timeout" ]; do
        sleep 1
        waited=$((waited + 1))
    done

    if kill -0 "$pid" 2>/dev/null; then
        echo -e "${YELLOW}⚠️  ${name} 未在 ${timeout}s 内退出，强制终止${NC}"
        kill -KILL "$pid"
        sleep 1
    fi

    rm -f "$pid_file"
    echo -e "${GREEN}✅ ${name} 已停止${NC}"
}

# 停止 Worker（先停 Worker，再停 API，避免任务执行到一半被中断）
stop_process "Worker" "$PID_DIR/worker.pid"

# 停止 API
stop_process "API" "$PID_DIR/api.pid"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  所有后端服务已停止${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "▶️  重新启动： ./scripts/start.sh"
echo ""
