"""
编辑工具基类与核心数据结构。

定义 ToolSpec（工具规格）和相关的数据结构，所有编辑工具都基于这些定义注册和执行。
工具的元信息（名称、描述、参数 Schema 等）界面和 Agent 共用，
避免两端重复定义导致不一致。

这是工具系统的"宪法"——定义了工具长什么样、怎么调用、有什么约定。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ToolRun

# 工具处理函数的类型签名：接收数据库会话和 ToolRun 记录，返回执行结果 dict
ToolHandler = Callable[[AsyncSession, ToolRun], Awaitable[dict]]

# 遮罩相关的参数由服务端从当轮选区填入，模型不需要知道这些
# 这些字段在生成工具描述给模型时会被剔除（见 agent/llm.py 的 _schema_of）
HIDDEN_MASK = ("mask_asset_id", "revision")


class UnknownTool(Exception):
    """工具不存在时抛出。"""


class LayerRef(BaseModel):
    """
    图层引用：指定要修改哪一层。

    与选区（MaskRef）正交：
    - 选区限定改哪块区域
    - layer_id 限定改哪一层
    - 两者同时存在时，只改该图层的该区域

    layer_id 可以是图层 id，也可以是图层名字（服务端会解析）。
    不填则作用在选区下最上层可见图像。
    """

    layer_id: str | None = Field(
        default=None,
        description=(
            "要修改的图层 id 或名字，如 background、subject、物体2。"
            "不填则作用在选区下最上层可见图像。"
        ),
    )


class MaskRef(BaseModel):
    """
    选区遮罩引用：指定要修改的区域。

    两个字段都由服务端填入，不暴露给模型。
    为什么不让模型自己填？
    - 选区是用户在前端通过点击/笔刷创建的，模型没法知道 mask_asset_id
    - revision 是服务端维护的版本号，模型不需要理解这个概念
    - 这些都是"基础设施"，模型只需要关心业务参数
    """

    mask_asset_id: str | None = None
    revision: int | None = None


@dataclass(frozen=True)
class ToolSpec:
    """
    一个工具的完整定义，界面与 Agent 共用。

    这是工具系统的核心数据结构，包含了工具的所有元信息和执行逻辑。
    UI 工具栏和 Agent 工具调用共享同一份定义，避免两端重复。

    为什么用 frozen dataclass？
    - 工具定义一旦注册就不应该被修改，不可变对象更安全
    - 可以作为 dict key 或放入集合
    - dataclass 比普通 class 更简洁，自动生成 __init__/__repr__/eq 等
    - 比 Pydantic 模型轻量，不需要序列化/反序列化能力

    字段说明：
    - name: 工具唯一标识（英文，用于代码和 API）
    - label: 显示名称（中文，给用户看）
    - description: 工具描述（给 Agent 模型看的，帮助模型理解什么时候用这个工具）
    - params: 参数的 Pydantic 模型，同时用于服务端校验和生成函数调用 Schema
    - handler: 执行函数，真正干活的地方
    - needs_approval: 是否需要用户确认才能执行（高风险工具设为 True）
    - queued: 是否走异步队列（像素级工具走队列，纯文档操作当场执行）
    - session_required: 是否需要编辑会话（有些工具可以独立使用）
    - agent_hidden: 对 Agent 隐藏的参数字段（服务端注入，不让模型填）
    - detail: 根据参数生成更详细的显示文本（如翻转要分水平/垂直）
    """

    name: str
    label: str
    description: str
    params: type[BaseModel]
    handler: ToolHandler
    needs_approval: bool = False
    # 只改 LayerDocument 的工具当场执行；像素工具仍走队列
    # 为什么区分？纯文档操作（改图层顺序、改透明度等）非常快，不需要排队
    # 像素操作（抠图、生成、调色等）慢，必须异步执行
    queued: bool = True
    session_required: bool = False
    # 素材 ID、随机种子这类参数应由服务端从上下文填入，不暴露给模型
    agent_hidden: tuple[str, ...] = field(default_factory=tuple)
    # 同一工具按参数细分说法，例如翻转要分水平与垂直，返回 None 则用 label
    detail: Callable[[dict], str | None] | None = None

    def label_for(self, params: dict | None = None) -> str:
        """
        根据参数生成显示用的工具标签。

        比如翻转工具，参数是水平就显示"水平翻转"，垂直就显示"垂直翻转"。
        如果 detail 函数返回 None，就用默认的 label。
        """
        refined = self.detail(params or {}) if self.detail else None
        return refined or self.label
