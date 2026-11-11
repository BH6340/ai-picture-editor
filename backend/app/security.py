"""
认证安全模块。

提供密码哈希、JWT Token 签发和校验功能。
所有认证相关的密码学操作都集中在这里，避免散落在各处。

为什么用 JWT + Cookie 的方式？
- JWT 是无状态的，服务端不需要存 session，扩展性好
- 放在 HttpOnly Cookie 里可以防止 XSS 攻击窃取 Token
- 比手动在前端管理 Token 更安全
"""

import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import get_settings

# Session Cookie 的名称，前端和后端都用这个名字
SESSION_COOKIE = "session"
# JWT 签名算法，HS256 是对称加密，用同一个密钥签名和验证
_ALGORITHM = "HS256"


def hash_password(plain: str) -> str:
    """
    对明文密码进行哈希，返回哈希后的字符串。

    使用 bcrypt 算法，自带 salt（每次哈希结果都不一样）。
    为什么用 bcrypt 而不是 MD5 / SHA256？
    - bcrypt 是专门为密码设计的，计算很慢，可以抵御暴力破解
    - 自带 salt，不需要单独管理 salt 字段
    - 可以通过调整 cost 参数调整计算强度，适应硬件发展
    """
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """
    验证明文密码是否与哈希值匹配。

    bcrypt.checkpw 内部会处理 salt 的提取和比较，
    并且使用常量时间比较（constant-time comparison），
    可以防止时序攻击（通过测量响应时间推断密码长度等信息）。
    """
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def issue_token(user_id: uuid.UUID) -> str:
    """
    签发 JWT Token，将用户 ID 编码到 Token 中。

    JWT payload 包含：
    - sub: subject，即用户 ID（JWT 标准字段）
    - exp: expiration time，过期时间（JWT 标准字段）

    为什么用 datetime.now(UTC) 而不是 datetime.utcnow()？
    - utcnow() 返回的是不带时区的 datetime，容易在不同时区产生歧义
    - now(UTC) 返回带时区的 datetime，JWT 库会正确处理
    - Python 3.12 开始 utcnow() 已被废弃
    """
    settings = get_settings()
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(UTC) + timedelta(hours=settings.jwt_ttl_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def read_token(token: str) -> uuid.UUID | None:
    """
    解析 JWT Token，返回用户 ID；任何无效情况都返回 None。

    容错设计：所有异常（签名错误、过期、格式错误、缺少 sub 字段等）
    统一返回 None，由调用方处理为"未认证"。

    为什么不区分不同的错误原因？
    - 安全考虑：不告诉攻击者 Token 具体哪里错了（是签名错了还是过期了）
    - 简化调用方：不需要 catch 一堆异常，只需要判断 None
    - 对用户来说都是"登录失效"，不需要区分原因
    """
    try:
        # jwt.decode 会自动校验签名和过期时间
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=[_ALGORITHM])
        return uuid.UUID(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        # InvalidTokenError: 签名错误、过期、格式错误等所有 JWT 相关异常
        # KeyError: payload 中没有 sub 字段
        # ValueError: sub 不是合法的 UUID
        return None
