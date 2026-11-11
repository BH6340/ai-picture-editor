"""
数据库连接与会话管理。

定义 SQLAlchemy 异步引擎、会话工厂和 FastAPI 依赖注入。
所有数据库操作都通过 SessionDep 获取会话，
确保连接池统一管理、事务边界清晰。

为什么用异步数据库？
- AI 场景是 IO 密集型，大部分时间在等数据库返回
- 异步模式下单线程可以并发处理多个请求，吞吐量更高
- 与 FastAPI 的 async 生态无缝集成
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    """
    SQLAlchemy 模型基类。

    所有数据模型都继承这个类，SQLAlchemy 通过它来管理 ORM 映射。
    为什么不直接用 DeclarativeBase？
    - 统一继承点：方便后续给所有模型加通用功能（如 __repr__、序列化方法等）
    - 类型标注更清晰
    """

    pass


# 创建异步数据库引擎
# engine 是连接池的管理者，负责创建、复用和回收数据库连接
# pool_pre_ping=True：每次从连接池取出连接时先 ping 一下，断了就自动重连
# 为什么需要 pool_pre_ping？数据库可能因为闲置超时、网络波动等原因断开连接，
# 如果不检测就直接用，会抛出 OperationalError，影响用户体验
engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)

# 异步会话工厂
# SessionFactory 是创建 AsyncSession 的工厂，每次调用返回一个新的会话
# expire_on_commit=False：提交事务后不使对象过期，对象仍然可以访问属性
# 为什么设为 False？默认情况下 commit 后对象会过期，再访问属性会触发查询，
# 但我们的 API 通常在 commit 后就返回响应，不需要再查数据库，
# 设为 False 可以避免不必要的查询，也方便在提交后直接使用对象数据
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """
    FastAPI 依赖注入函数：提供数据库会话。

    使用异步生成器模式（yield），FastAPI 会：
    1. 在请求开始时创建一个新的 session
    2. 把 session 注入到路由处理函数中
    3. 请求结束后自动关闭 session（不管成功还是异常）

    为什么用生成器而不是直接返回 session？
    - 自动清理：yield 之后的代码在请求结束时执行，确保 session 被关闭
    - 事务管理：可以在这里统一控制 commit / rollback（当前项目在 service 层手动管理）
    """
    async with SessionFactory() as session:
        yield session


# SessionDep 是 FastAPI 依赖的类型别名
# 在路由函数参数中直接写 SessionDep 就能注入数据库会话
# 为什么用 Annotated？这是 FastAPI 推荐的写法，把依赖信息放在类型注解里，
# 函数签名更干净，也方便类型检查工具正确推断
SessionDep = Annotated[AsyncSession, Depends(get_session)]
