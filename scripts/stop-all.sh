#!/usr/bin/env bash
# ============================================================
# 全栈停止脚本（前端 + 后端 + Docker 中间件）
# 使用方式：
#   ./scripts/stop-all.sh          # 停止所有服务，保留 Docker 中间件
#   ./scripts/stop-all.sh --all    # 停止所有服务，也停止 Docker 中间件
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_DIR/logs"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# 是否停止 Docker 中间件
STOP_DOCKER=false
if [ "$1" = "--all" ] || [ "$1" = "-a" ]; then
    STOP_DOCKER=true
fi

echo -e "${YELLOW}"
echo "╔══════════════════════════════════════════╗"
echo "║   AI 修图智能体 - 停止所有服务           ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"
echo ""

cd "$PROJECT_DIR"

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

# ==================== 第一步：停止前端 ====================
echo -e "${YELLOW}【1/3】停止前端服务${NC}"
echo "─────────────────────────────────────────────"
stop_process "前端" "$LOG_DIR/frontend.pid" 5
echo ""

# ==================== 第二步：停止后端 ====================
echo -e "${YELLOW}【2/3】停止后端服务（API + Worker）${NC}"
echo "─────────────────────────────────────────────"
cd "$PROJECT_DIR/backend"
if [ -f "scripts/stop.sh" ]; then
    bash scripts/stop.sh
else
    stop_process "API" "$PROJECT_DIR/backend/logs/api.pid"
    stop_process "Worker" "$PROJECT_DIR/backend/logs/worker.pid"
fi
echo ""

# ==================== 第三步：停止 Docker 中间件（可选）====================
if [ "$STOP_DOCKER" = true ]; then
    echo -e "${YELLOW}【3/3】停止 Docker 中间件${NC}"
    echo "─────────────────────────────────────────────"
    cd "$PROJECT_DIR"
    docker compose down
    echo -e "${GREEN}✅ Docker 中间件已停止${NC}"
else
    echo -e "${YELLOW}【3/3】Docker 中间件${NC}"
    echo "─────────────────────────────────────────────"
    echo "💡 保留 Docker 中间件运行中（加 --all 参数可一并停止）"
    echo "   停止命令：docker compose down"
fi

echo ""
echo -e "${GREEN}"
echo "╔══════════════════════════════════════════╗"
echo "║   ✅ 所有服务已停止                      ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"
echo ""
echo "▶️  重新启动： ./scripts/start-all.sh"
echo ""
