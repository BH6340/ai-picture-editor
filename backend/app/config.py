"""
应用配置管理。

基于 pydantic-settings 实现，从环境变量和 .env 文件读取配置。
所有模块通过 get_settings() 获取配置实例，确保配置只读且全局唯一。

为什么用 pydantic-settings 而不是普通 dict / os.environ？
- 类型安全：自动类型转换和校验，配错了启动时就报错
- 默认值集中管理：每个配置项都有合理的开发环境默认值
- 支持 .env 文件：开发环境不用手动 export 一堆环境变量
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（backend/app/config.py 的上两级就是 backend/ 的上一级，即项目根）
# 用 __file__ 计算而不是硬编码路径，确保在任何工作目录下都能正确定位
ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """
    应用配置模型。

    每个字段对应一个环境变量，pydantic-settings 会自动从环境变量和 .env 文件中读取。
    字段名用 snake_case，环境变量名也用对应的全大写下划线形式（如 database_url → DATABASE_URL）。

    model_config 说明：
    - env_file: 指定从哪个 .env 文件读取（项目根目录下的 .env）
    - extra="ignore": 环境变量中多余的字段直接忽略，不报错
    """

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # 运行环境：development / production，影响静态文件托管等行为
    app_env: str = "development"
    # API 服务监听端口
    api_port: int = 7302

    # PostgreSQL 数据库连接串，使用 asyncpg 异步驱动
    # 格式：postgresql+asyncpg://用户名:密码@主机:端口/数据库名
    database_url: str = "postgresql+asyncpg://retouch:retouch_dev@localhost:7311/retouch"
    # Redis 连接串，用于队列、Pub/Sub、缓存
    redis_url: str = "redis://localhost:7312"

    # MinIO / S3 兼容对象存储地址
    s3_endpoint: str = "http://localhost:7313"
    # 浏览器访问签名 URL 的地址；空则与 s3_endpoint 相同
    # 为什么需要单独的 public endpoint？
    # 生产环境中，服务内部可能走内网地址访问 S3，但浏览器需要走公网域名
    s3_public_endpoint: str = ""
    s3_access_key: str = "retouch"
    s3_secret_key: str = "retouch_dev"
    s3_bucket: str = "retouch"
    # 预签名 URL 的有效期，单位秒。900 秒 = 15 分钟
    s3_url_ttl: int = 900

    # JWT 签名密钥。HS256 算法要求密钥不短于 32 字节
    # 开发环境用默认值，生产环境必须修改
    jwt_secret: str = "dev-only-secret-please-change-in-production"
    # JWT Token 有效期，单位小时
    jwt_ttl_hours: int = 24

    # AI 图片服务提供者：mock（本地占位图，不调用 API） / dashscope（阿里云百炼）
    image_provider: str = "mock"
    dashscope_api_key: str = ""
    # 百炼 API 基础地址；业务空间专属域名格式为 https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com
    dashscope_base_url: str = "https://dashscope.aliyuncs.com"
    # 文生图模型
    text_to_image_model: str = "qwen-image-3.0-pro"
    # 图片编辑模型
    image_edit_model: str = "qwen-image-edit-max"
    # 规划模型（纯文本，用于生成修图计划）
    planner_model: str = "qwen-plus"
    # 抠图提供者：auto（有 rembg 用 rembg，否则四角抠图） / corner（四角抠图）
    # 测试时强制 corner，避免自动下载 rembg 模型
    matting_provider: str = "auto"
    # OCR 提供者：auto（有 rapidocr 就识别文字层） / none（跳过）
    # 测试时强制 none，避免加载 OCR 模型
    ocr_provider: str = "auto"

    @property
    def is_production(self) -> bool:
        """是否为生产环境。用于判断是否托管前端静态文件等。"""
        return self.app_env == "production"

    @property
    def frontend_dist(self) -> Path:
        """前端构建产物目录路径。生产环境下 FastAPI 会托管这个目录的静态文件。"""
        return ROOT_DIR / "frontend" / "dist"


@lru_cache
def get_settings() -> Settings:
    """
    获取全局配置实例。

    使用 lru_cache 装饰器确保只创建一次 Settings 实例（单例模式）。
    为什么用 lru_cache 而不是模块级变量？
    - 延迟初始化：第一次调用时才读取环境变量，测试时可以先改环境变量再调用
    - 方便测试：测试中可以调用 cache_clear() 重置配置，测试不同的配置组合
    - 线程安全：lru_cache 在首次调用后缓存结果，后续调用直接返回
    """
    return Settings()
