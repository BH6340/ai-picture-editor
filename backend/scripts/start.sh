#!/usr/bin/env bash
# ============================================================
# 后端一键启动脚本（API + Worker，后台运行）
# 使用方式：./scripts/start.sh
# ============================================================

set -e

# 脚本所在目录（backend/）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$BACKEND_DIR/logs"
PID_DIR="$BACKEND_DIR/logs"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  启动后端服务（API + Worker）${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# 进入 backend 目录
cd "$BACKEND_DIR"

# 创建日志目录
mkdir -p "$LOG_DIR"

# 检查 uv 是否可用
if ! command -v uv &> /dev/null; then
    echo -e "${RED}❌ 未找到 uv 命令，请先安装 uv${NC}"
    echo "安装命令：curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# 检查 .env 文件
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}⚠️  .env 文件不存在，正在从 .env.example 复制...${NC}"
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "${GREEN}✅ 已创建 .env 文件（默认 mock 模式）${NC}"
    else
        echo -e "${RED}❌ .env.example 也不存在，请手动创建 .env${NC}"
        exit 1
    fi
fi

# 检查虚拟环境
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}⚠️  虚拟环境不存在，正在安装依赖...${NC}"
    uv sync --all-extras
    echo -e "${GREEN}✅ 依赖安装完成${NC}"
fi

# ---- 启动 API 服务 ----
API_PID_FILE="$PID_DIR/api.pid"
API_LOG="$LOG_DIR/api.log"

if [ -f "$API_PID_FILE" ] && kill -0 "$(cat "$API_PID_FILE")" 2>/dev/null; then
    echo -e "${YELLOW}⚠️  API 服务已在运行（PID: $(cat "$API_PID_FILE")），跳过启动${NC}"
else
    echo "🚀 启动 API 服务（端口 7302）..."
    nohup uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 7302 > "$API_LOG" 2>&1 &
    echo $! > "$API_PID_FILE"
    sleep 2

    if kill -0 "$(cat "$API_PID_FILE")" 2>/dev/null; then
        echo -e "${GREEN}✅ API 服务启动成功（PID: $(cat "$API_PID_FILE")）${NC}"
        echo "   日志文件：$API_LOG"
    else
        echo -e "${RED}❌ API 服务启动失败，请查看日志：$API_LOG${NC}"
        tail -20 "$API_LOG"
        exit 1
    fi
fi

# ---- 启动 Worker 服务 ----
WORKER_PID_FILE="$PID_DIR/worker.pid"
WORKER_LOG="$LOG_DIR/worker.log"

if [ -f "$WORKER_PID_FILE" ] && kill -0 "$(cat "$WORKER_PID_FILE")" 2>/dev/null; then
    echo -e "${YELLOW}⚠️  Worker 服务已在运行（PID: $(cat "$WORKER_PID_FILE")），跳过启动${NC}"
else
    echo "🚀 启动 Worker 服务（ARQ 异步任务队列）..."
    nohup uv run arq app.worker.WorkerSettings > "$WORKER_LOG" 2>&1 &
    echo $! > "$WORKER_PID_FILE"
    sleep 2

    if kill -0 "$(cat "$WORKER_PID_FILE")" 2>/dev/null; then
        echo -e "${GREEN}✅ Worker 服务启动成功（PID: $(cat "$WORKER_PID_FILE")）${NC}"
        echo "   日志文件：$WORKER_LOG"
    else
        echo -e "${RED}❌ Worker 服务启动失败，请查看日志：$WORKER_LOG${NC}"
        tail -20 "$WORKER_LOG"
        exit 1
    fi
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  所有后端服务启动完成！${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "📡 API 地址：  http://localhost:7302"
echo "💓 健康检查：  http://localhost:7302/api/health"
echo ""
echo "📄 查看 API 日志：    tail -f logs/api.log"
echo "📄 查看 Worker 日志： tail -f logs/worker.log"
echo ""
echo "⏹  停止服务： ./scripts/stop.sh"
echo ""
