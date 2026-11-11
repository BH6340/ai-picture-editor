"""
事件总线：基于 Redis Pub/Sub 的发布订阅机制。

Worker 进程执行工具时，通过 publish() 把进度事件发到 Redis；
API 服务的 SSE 路由通过 subscribe() 订阅事件，实时推送给前端。

这是 Worker 和 API 服务之间的"桥梁"——两个独立进程之间
通过 Redis 中转消息，实现实时进度推送。

为什么用 Redis Pub/Sub 而不是直接 SSE？
- Worker 和 API 服务是独立的进程，可能部署在不同机器上
- 需要一个中间件来传递消息，Redis 已经在项目中用了（ARQ 队列），不用额外引入
- Pub/Sub 是一对多的，多个连接可以同时订阅同一个 run 的进度
"""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from redis.asyncio import Redis

from app.config import get_settings

# 空闲心跳间隔（秒）
# 如果这么长时间没有消息，就产出一个 None，让 SSE 发送心跳
# 为什么需要心跳？
# - SSE 连接如果长时间没有数据，可能被中间代理（Nginx、CDN 等）断开
# - 心跳可以保持连接活跃，也让前端知道连接还在
IDLE_TICK = 15.0


@lru_cache
def redis_client() -> Redis:
    """
    获取 Redis 客户端实例（单例）。

    用 lru_cache 缓存，确保全局只有一个 Redis 连接池。
    decode_responses=True：自动将 bytes 解码为 str，不用每次手动 decode。
    """
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


def _channel(run_id: uuid.UUID) -> str:
    """生成某个 run 对应的 Redis 频道名。每个 run 有自己独立的频道。"""
    return f"run:{run_id}"


async def publish(run_id: uuid.UUID, payload: dict) -> None:
    """
    发布一条进度事件到指定 run 的频道。

    Args:
        run_id: 工具运行 ID，确定发到哪个频道
        payload: 事件数据，会被序列化为 JSON 字符串

    Worker 在执行工具的各个阶段调用这个函数推送进度，
    比如开始执行、进度更新、执行完成等。
    """
    await redis_client().publish(_channel(run_id), json.dumps(payload))


@asynccontextmanager
async def subscribe(run_id: uuid.UUID) -> AsyncIterator[AsyncIterator[dict | None]]:
    """
    订阅某个 run 的进度事件，返回异步迭代器。

    使用方式（在 SSE 路由中）：
    ```
    async with subscribe(run_id) as messages:
        async for message in messages:
            if message is None:
                # 心跳事件
                yield "heartbeat"
            else:
                yield f"event: {message['type']}\\ndata: ..."
    ```

    迭代器产出：
    - dict: 正常的事件数据
    - None: 心跳（空闲超过 IDLE_TICK 秒时产出）

    为什么用 async context manager？
    - 确保订阅在使用完后正确关闭（unsubscribe + close pubsub）
    - 即使发生异常也能清理资源
    - 调用方用 async with 就能自动管理生命周期

    为什么空闲时产出 None 而不是直接跳过？
    - 让 SSE 路由可以发送心跳注释行，保持连接活跃
    - 调用方可以根据 None 判断连接是否正常
    - 这是"拉模式"的心跳——调用方控制节奏，pubsub 只负责等消息
    """
    channel = _channel(run_id)
    # 创建 pubsub 对象并订阅频道
    pubsub = redis_client().pubsub()
    await pubsub.subscribe(channel)

    async def messages() -> AsyncIterator[dict | None]:
        """内部异步生成器：持续接收消息，空闲时产出 None。"""
        while True:
            # get_message 会阻塞最多 timeout 秒
            # 有消息时返回消息 dict，超时返回 None
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=IDLE_TICK)
            # 有消息就解析 JSON 并产出；超时就产出 None 作为心跳
            yield json.loads(message["data"]) if message else None

    try:
        yield messages()
    finally:
        # 退出时关闭 pubsub 连接
        await pubsub.aclose()
