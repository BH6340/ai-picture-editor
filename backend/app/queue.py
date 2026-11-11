"""
ARQ 异步任务队列配置与投递。

ARQ 是基于 Redis 的异步任务队列，Worker 进程监听队列并执行任务。
API 服务通过 enqueue() 投递任务，Worker 收到后执行。

为什么用 ARQ 而不是 Celery？
- 基于 Redis 和 asyncio，和 FastAPI 技术栈统一
- 比 Celery 轻量，配置简单
- 项目规模不需要 Celery 的复杂特性
"""

import uuid

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import get_settings

# 全局队列连接池，懒加载
# 为什么用模块级变量而不是每次创建？
# - 连接池是昂贵的资源，应该复用
# - 第一次调用 queue() 时才创建（延迟初始化）
# - close_queue() 可以手动关闭
_pool: ArqRedis | None = None


async def queue() -> ArqRedis:
    """
    获取 ARQ 队列连接池（懒加载单例）。

    第一次调用时创建连接池，后续调用复用。
    为什么是 async 的？因为创建连接池需要异步连接 Redis。
    """
    global _pool
    if _pool is None:
        # 从配置的 Redis URL 解析出 RedisSettings
        # ARQ 的 RedisSettings.from_dsn 可以直接从 URL 解析主机、端口、密码等
        _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _pool


async def close_queue() -> None:
    """
    关闭队列连接池。

    在应用关闭时调用（main.py 的 lifespan 中），确保 Redis 连接被正确释放。
    """
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def enqueue(task: str, run_id: uuid.UUID) -> None:
    """
    投递一个异步任务到队列。

    Args:
        task: 任务函数名（Worker 端注册的函数名，如 'run_tool'）
        run_id: 工具运行 ID，作为任务参数，同时也作为 job_id

    用 run_id 作为 _job_id 的原因：
    - 幂等性：重复投递同一个 run_id 不会产生第二次执行
    - 方便追踪：可以通过 run_id 查询任务状态
    - 业务 ID 和任务 ID 统一，不需要额外映射

    ARQ 的 _job_id 机制：如果同一个 job_id 的任务已经存在（pending 或 running），
    再次 enqueue 不会重复投递，直接返回已有的 job。
    """
    pool = await queue()
    await pool.enqueue_job(task, run_id, _job_id=str(run_id))
