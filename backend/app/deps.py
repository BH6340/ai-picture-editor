"""
FastAPI 依赖注入：认证相关依赖。

提供当前用户获取的依赖函数 CurrentUser，
所有需要登录的接口都通过它注入当前登录用户。

为什么把认证逻辑单独放 deps.py？
- 依赖注入是 FastAPI 的核心概念，集中管理便于复用
- 路由层只需要写 CurrentUser 就能拿到用户，不用关心具体实现
- 修改认证方式（比如换成 Session）只需要改这里，不影响路由
"""

from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status

from app.db import SessionDep
from app.models import User
from app.security import SESSION_COOKIE, read_token
from app.services import auth as auth_service

# 未认证时统一抛出的异常对象
# 为什么用模块级常量而不是每次 raise 新对象？
# - 节省内存：不需要每次请求都创建新的异常对象
# - 一致性：所有未认证的错误响应完全相同
_UNAUTHENTICATED = HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录或会话已过期")


async def current_user(
    session: SessionDep,
    token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> User:
    """
    从 Cookie 中读取 Session Token，校验并返回当前登录用户。

    执行流程：
    1. 从 Cookie 中读取 session 字段（如果没有，直接返回未认证）
    2. 用 read_token 解析 JWT，得到用户 ID
    3. 根据用户 ID 从数据库查询用户对象
    4. 如果任何一步失败，抛出 401 未认证异常

    为什么参数用 Annotated + Cookie 而不是直接写 Cookie？
    - 这是 FastAPI 的推荐写法，把依赖信息放在类型注解中
    - alias=SESSION_COOKIE：Cookie 的实际名称和变量名可以不一样
    - 默认值 None：Cookie 不存在时不会报错，而是返回 None，由我们自己处理

    注意：这个函数依赖 SessionDep（数据库会话），
    这是依赖的嵌套——一个依赖可以依赖另一个依赖。
    """
    # Cookie 中没有 Token，说明未登录
    if token is None:
        raise _UNAUTHENTICATED

    # 解析 Token，失败返回 None
    user_id = read_token(token)
    if user_id is None:
        raise _UNAUTHENTICATED

    # 根据用户 ID 查询数据库
    user = await auth_service.get_by_id(session, user_id)
    if user is None:
        raise _UNAUTHENTICATED
    return user


# CurrentUser 是 FastAPI 依赖的类型别名
# 在路由函数参数中直接写 CurrentUser 就能注入当前登录用户
# 例如：async def my_endpoint(user: CurrentUser): ...
# 为什么用 Annotated？和 SessionDep 一样的道理：
# - 把依赖信息放在类型里，函数签名更干净
# - 类型检查工具能正确推断 User 类型
CurrentUser = Annotated[User, Depends(current_user)]
