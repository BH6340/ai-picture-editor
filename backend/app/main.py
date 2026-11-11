"""
FastAPI 应用入口。

创建 FastAPI 实例、注册路由、配置 lifespan 启动/关闭逻辑。
这是整个后端应用的"根"，所有模块都在这里组装。

为什么 main.py 这么短？
- 遵循 FastAPI 的最佳实践：入口只做组装，业务逻辑在 routers/services 中
- 各模块职责单一，main.py 只负责"把零件拼起来"
- 便于测试：可以单独测试 router / service，不需要启动整个应用
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

from app import storage
from app.config import get_settings
from app.queue import close_queue
from app.routers import assets, auth, batches, events, health, runs, sessions

# 获取全局配置实例
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """
    应用生命周期管理：启动时执行初始化，关闭时执行清理。

    FastAPI 的 lifespan 替代了旧版的 startup / shutdown 事件。
    使用 async context manager 模式：
    - yield 之前：应用启动时执行（初始化资源）
    - yield 之后：应用关闭时执行（释放资源）

    当前做的事情：
    1. 启动时：确保 S3 存储桶存在（在独立线程中执行，避免阻塞事件循环）
    2. 关闭时：关闭 ARQ 队列连接池

    为什么 storage.ensure_bucket 要包在 asyncio.to_thread 里？
    - boto3 是同步库，直接在异步函数中调用会阻塞整个事件循环
    - asyncio.to_thread 把同步操作放到线程池里执行，不阻塞事件循环
    - 这是"异步代码中调用同步库"的标准做法
    """
    await asyncio.to_thread(storage.ensure_bucket)
    yield
    await close_queue()


# 创建 FastAPI 应用实例
# - docs_url: Swagger UI 文档地址
# - openapi_url: OpenAPI Schema 地址（Swagger UI 从这里读取接口定义）
# - lifespan: 生命周期管理器，处理启动/关闭逻辑
app = FastAPI(
    title="AI 修图智能体",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# 创建 /api 前缀的路由组，所有业务 API 都挂在这个前缀下
# 为什么需要 /api 前缀？
# - 区分 API 和静态资源，便于反向代理配置
# - 前端开发时可以代理 /api 到后端，其他路径走 Vite dev server
api = APIRouter(prefix="/api")
api.include_router(health.router)
api.include_router(auth.router)
api.include_router(assets.router)
api.include_router(runs.router)
api.include_router(sessions.router)
api.include_router(batches.router)
app.include_router(api)

# SSE 事件路由不挂在 /api 下，直接挂在根路径
# 为什么 SSE 要单独挂？
# - 反向代理（Nginx 等）通常会对 API 路径开启缓冲（gzip 等）
# - SSE 需要实时推送，缓冲会导致消息延迟甚至丢失
# - 单独的路径前缀便于针对性地关闭缓冲、配置超时等
app.include_router(events.router)

# 生产环境下托管前端静态文件
# 开发环境走 Vite dev server（通过 vite.config.ts 的 proxy 代理 API）
# 为什么要让 FastAPI 托管前端？
# - 生产环境前后端同源部署，不需要额外的 Nginx 配置
# - 简化部署：一个服务同时提供 API 和前端页面
# - html=True：开启 SPA 模式，所有未匹配的路径都返回 index.html（前端路由接管）
if settings.frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="frontend")
