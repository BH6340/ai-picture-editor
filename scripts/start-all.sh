#!/usr/bin/env bash
# ============================================================
# 全栈一键启动脚本（Docker 中间件 + 后端 API + Worker + 前端）
# 使用方式：./scripts/start-all.sh
# ============================================================

set -e

# 项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_DIR/logs"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════╗"
echo "║   AI 修图智能体 - 全栈一键启动           ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"
echo ""

cd "$PROJECT_DIR"
mkdir -p "$LOG_DIR"

# ==================== 第一步：启动 Docker 中间件 ====================
echo -e "${GREEN}【1/4】启动 Docker 中间件（PostgreSQL + Redis + MinIO）${NC}"
echo "─────────────────────────────────────────────"

# 检查 Docker 是否可用
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ 未找到 docker 命令，请先安装 Docker${NC}"
    exit 1
fi

# 检查 Docker daemon 是否运行
if ! docker info &> /dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  Docker daemon 未运行，正在尝试启动...${NC}"
    # 尝试 systemctl（云服务器）
    if command -v systemctl &> /dev/null && systemctl list-unit-files | grep -q docker; then
        sudo systemctl start docker
    # 尝试 service（WSL）
    elif command -v service &> /dev/null && [ -f /etc/init.d/docker ]; then
        sudo service docker start
    else
        echo -e "${RED}❌ 无法启动 Docker，请手动启动后重试${NC}"
        echo "   WSL:        sudo service docker start"
        echo "   云服务器:   sudo systemctl start docker"
        exit 1
    fi
    sleep 2
fi

# 启动 Docker Compose 服务
if docker compose ps postgres 2>/dev/null | grep -q "Up"; then
    echo -e "${GREEN}✅ PostgreSQL 已在运行${NC}"
else
    echo "🐘 启动 PostgreSQL..."
fi

if docker compose ps redis 2>/dev/null | grep -q "Up"; then
    echo -e "${GREEN}✅ Redis 已在运行${NC}"
else
    echo "🔴 启动 Redis..."
fi

if docker compose ps minio 2>/dev/null | grep -q "Up"; then
    echo -e "${GREEN}✅ MinIO 已在运行${NC}"
else
    echo "📦 启动 MinIO..."
fi

docker compose up -d

# 等待中间件就绪
echo "⏳ 等待中间件就绪..."
max_wait=30
waited=0
while [ "$waited" -lt "$max_wait" ]; do
    if docker compose ps postgres 2>/dev/null | grep -q "healthy" && \
       docker compose ps redis 2>/dev/null | grep -q "healthy" && \
       docker compose ps minio 2>/dev/null | grep -q "healthy"; then
        echo -e "${GREEN}✅ 所有中间件就绪${NC}"
        break
    fi
    sleep 2
    waited=$((waited + 2))
done

if [ "$waited" -ge "$max_wait" ]; then
    echo -e "${YELLOW}⚠️  中间件可能尚未完全就绪，继续启动中...${NC}"
    docker compose ps
fi

echo ""

# ==================== 第二步：初始化数据库 ====================
echo -e "${GREEN}【2/4】数据库初始化${NC}"
echo "─────────────────────────────────────────────"

cd "$PROJECT_DIR/backend"

# 检查 .env（在项目根目录，config.py 从那里读取）
ENV_FILE="$PROJECT_DIR/.env"
ENV_EXAMPLE="$PROJECT_DIR/.env.example"
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${YELLOW}⚠️  .env 文件不存在，正在从 .env.example 复制...${NC}"
    if [ -f "$ENV_EXAMPLE" ]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        echo -e "${GREEN}✅ 已创建 .env 文件（默认 mock 模式）${NC}"
    fi
fi

# 检查虚拟环境
if [ ! -d ".venv" ]; then
    echo "📦 安装后端依赖..."
    uv sync --all-extras
fi

# 执行数据库迁移
echo "🔄 执行数据库迁移..."
uv run alembic upgrade head
echo -e "${GREEN}✅ 数据库迁移完成${NC}"
echo ""

# ==================== 第三步：启动后端服务 ====================
echo -e "${GREEN}【3/4】启动后端服务（API + Worker）${NC}"
echo "─────────────────────────────────────────────"

cd "$PROJECT_DIR/backend"
bash scripts/start.sh

echo ""

# ==================== 第四步：启动前端 ====================
echo -e "${GREEN}【4/4】启动前端服务（Vite Dev Server）${NC}"
echo "─────────────────────────────────────────────"

cd "$PROJECT_DIR/frontend"
FRONTEND_PID_FILE="$LOG_DIR/frontend.pid"
FRONTEND_LOG="$LOG_DIR/frontend.log"

if [ -f "$FRONTEND_PID_FILE" ] && kill -0 "$(cat "$FRONTEND_PID_FILE")" 2>/dev/null; then
    echo -e "${YELLOW}⚠️  前端服务已在运行（PID: $(cat "$FRONTEND_PID_FILE")），跳过启动${NC}"
else
    # 检查 node_modules
    if [ ! -d "node_modules" ]; then
        echo "📦 安装前端依赖..."
        npm install
    fi

    echo "🎨 启动前端开发服务器（端口 7301）..."
    nohup npm run dev > "$FRONTEND_LOG" 2>&1 &
    echo $! > "$FRONTEND_PID_FILE"
    sleep 3

    if kill -0 "$(cat "$FRONTEND_PID_FILE")" 2>/dev/null; then
        echo -e "${GREEN}✅ 前端服务启动成功（PID: $(cat "$FRONTEND_PID_FILE")）${NC}"
        echo "   日志文件：$FRONTEND_LOG"
    else
        echo -e "${RED}❌ 前端服务启动失败，请查看日志：$FRONTEND_LOG${NC}"
        tail -20 "$FRONTEND_LOG"
    fi
fi

echo ""

# ==================== 启动完成 ====================
echo -e "${GREEN}"
echo "╔══════════════════════════════════════════╗"
echo "║   🎉 所有服务启动完成！                  ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"
echo ""
echo "🌐 前端地址：    http://localhost:7301"
echo "📖 API 文档：    http://localhost:7302/api/docs"
echo "💓 健康检查：    http://localhost:7302/api/health"
echo "🗄  MinIO 控制台： http://localhost:7314"
echo "   账号：retouch / 密码：retouch_dev"
echo ""
echo "📄 日志目录：    $LOG_DIR"
echo ""
echo "⏹  停止所有服务： ./scripts/stop-all.sh"
echo ""
