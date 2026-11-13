# ============================================================
# 多阶段构建 Dockerfile
# ============================================================
#
# 【什么是多阶段构建？】
# 一个 Dockerfile 里可以有多个 FROM，每个 FROM 是一个构建阶段
# 最终镜像只包含最后一个阶段的内容，前面阶段的中间产物可以被复制过来
# 好处：最终镜像体积小，不包含编译工具、源码等不需要的东西
#
# 【本文件的两个阶段】
#   阶段 1 (frontend): 用 Node.js 构建前端静态产物
#   阶段 2 (python-base): Python 运行时 + 后端代码 + 前端产物
#
# 【为什么前端和后端打在同一个镜像里？】
# 因为后端 FastAPI 会同源托管前端静态文件（SPA 单页应用）
# 这样部署只需要一个服务，不用单独配 Nginx 托管前端
# ============================================================


# ============================================================
# 阶段一：前端构建
# ============================================================
# 用 node:22-alpine 作为构建环境
# AS frontend 给这个阶段起个名字，后面可以用 --from=frontend 引用
FROM node:22-alpine AS frontend

# 工作目录：所有后续命令都在这个目录下执行
WORKDIR /build

# 先复制 package.json 和 package-lock.json
# 【Docker 缓存技巧】
#   Docker 构建是分层的，每一行命令对应一层
#   如果文件没变，就会直接用缓存，不会重新执行
#   所以先复制依赖文件（变化少），再复制源码（变化多）
#   这样只要 package.json 没变，npm ci 就会走缓存，大大加快构建速度
COPY frontend/package*.json ./

# 安装依赖
# npm ci（clean install）vs npm install 的区别：
#   npm ci    - 严格按照 package-lock.json 安装，版本完全一致，速度更快
#               但不会修改 package-lock.json，适合 CI/CD 和生产构建
#   npm install - 会根据 package.json 解析版本，可能更新 lock 文件
#               适合开发时添加/更新依赖
RUN npm ci

# 复制前端源码
COPY frontend/ ./

# 构建生产环境静态产物
# 产物会输出到 dist/ 目录下
RUN npm run build


# ============================================================
# 阶段二：Python 运行时（最终镜像）
# ============================================================
# 用 python:3.13-slim 作为基础镜像
# slim vs alpine vs 完整版：
#   python:3.13         - 完整版，约 1GB，包含所有编译工具
#   python:3.13-slim    - 精简版，约 200MB，只保留运行时
#   python:3.13-alpine  - Alpine 版，约 100MB，最小但兼容性有坑（musl libc）
# 这里选 slim，因为 alpine 的 musl libc 和一些 Python C 扩展不兼容
# （比如 numpy、pandas 等在 alpine 上可能有问题）
FROM python:3.13-slim

# 从 uv 官方镜像复制 uv 二进制文件
# 这样不用在镜像里安装 uv，直接拿编译好的二进制
# ghcr.io 是 GitHub Container Registry
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 安装系统依赖
# 1. 更新 apt 包索引
# 2. 安装文泉驿微米黑中文字体（AI 生成的图如果有中文，需要字体渲染）
# 3. --no-install-recommends：不安装推荐的可选包，减小镜像体积
# 4. 删除 apt 缓存，进一步减小镜像体积
# 【为什么写成一行？】
#   Docker 每一行 RUN 是一层，写成一行可以减少层数
#   而且 apt-get update 和 install 必须在同一层，否则 update 的缓存可能过期
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/*

# 工作目录：后端代码目录
WORKDIR /app/backend

# 设置环境变量
#   UV_COMPILE_BYTECODE=1  - 安装 Python 包时预编译 .pyc 字节码，启动更快
#   UV_LINK_MODE=copy      - 用 copy 模式而不是 symlink，兼容性更好
#   PYTHONPATH=/app/backend - Python 模块搜索路径，让 import app.xxx 能找到
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONPATH=/app/backend

# 先复制依赖文件（利用缓存，同前端的道理）
COPY backend/pyproject.toml backend/uv.lock ./

# 安装 Python 依赖
#   --frozen      - 严格按照 uv.lock 安装，不更新锁文件
#   --all-extras  - 安装所有 extra 依赖（cv、agent 等）
#   --no-dev      - 不安装开发依赖（pytest、ruff 等），生产环境不需要
RUN uv sync --frozen --all-extras --no-dev

# 复制后端源码
COPY backend/ ./

# 从前端构建阶段复制静态产物到 Python 镜像里
# 目标路径 /app/frontend/dist，后端会从这里托管前端文件
COPY --from=frontend /build/dist /app/frontend/dist

# 把虚拟环境的 bin 目录加到 PATH
# 这样 uvicorn、arq 等命令可以直接用，不用写完整路径
ENV PATH="/app/backend/.venv/bin:$PATH"

# 声明容器监听的端口（仅作文档说明，不会自动暴露）
# 真正暴露端口靠 docker run -p 或 docker-compose 的 ports
EXPOSE 7302

# 【注意】这里没有 CMD / ENTRYPOINT
# 因为这个镜像有两种用法：
#   1. API 服务 - 用 uvicorn 启动
#   2. Worker 服务 - 用 arq 启动
# 具体启动命令由 docker-compose.yml 的 command 指定
