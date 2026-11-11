"""
会话相关的 Pydantic Schema 定义。

包含创建会话、选区、工具调用等接口的请求和响应模型。
所有模型都加了 example，在 Swagger UI 中可以直接看到示例值，方便调试。
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field

from app.layers import LayerDocument
from app.models import EditHistory, EditSession
from app.schemas.asset import AssetOut
from app.schemas.run import RunOut

MAX_WALL_ASSETS = 12


class SessionCreateIn(BaseModel):
    """创建编辑会话的请求参数。"""

    current_asset_id: uuid.UUID = Field(
        ...,
        json_schema_extra={
            "example": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        },
        description="当前使用的素材 ID，即画布上显示的主图",
    )
    # 同批未采用的候选一并进图片墙
    asset_ids: Annotated[list[uuid.UUID], Field(max_length=MAX_WALL_ASSETS)] = Field(
        default=[],
        json_schema_extra={
            "example": [
                "b2c3d4e5-f6a7-8901-bcde-f12345678901",
                "c3d4e5f6-a7b8-9012-cdef-123456789012",
            ],
        },
        description=f"同批候选素材 ID 列表，会显示在图片墙中，最多 {MAX_WALL_ASSETS} 张",
    )
    title: str | None = Field(
        default=None,
        json_schema_extra={"example": "白色背景产品主图精修"},
        description="会话标题，不传则用默认标题",
    )


class SessionPatchIn(BaseModel):
    """修改会话信息的请求参数。"""

    title: str | None = Field(
        default=None,
        json_schema_extra={"example": "产品主图精修 v2"},
        description="新的会话标题",
    )
    current_asset_id: uuid.UUID | None = Field(
        default=None,
        json_schema_extra={
            "example": "d4e5f6a7-b8c9-0123-defa-234567890123",
        },
        description="切换当前使用的素材 ID",
    )


class ToolInvokeIn(BaseModel):
    """手动调用工具的请求参数。"""

    tool: str = Field(
        ...,
        json_schema_extra={"example": "adjust_image"},
        description="工具名称，如 adjust_image、remove_background 等",
    )
    params: dict = Field(
        default={},
        json_schema_extra={
            "example": {"brightness": 10, "contrast": 5, "saturation": 3},
        },
        description="工具参数字典，具体字段取决于调用的工具",
    )


class PointIn(BaseModel):
    """选区分点坐标，使用相对坐标（0-1）。"""

    x: float = Field(
        ge=0, le=1,
        json_schema_extra={"example": 0.35},
        description="横坐标，相对于画布宽度的比例，0 为最左，1 为最右",
    )
    y: float = Field(
        ge=0, le=1,
        json_schema_extra={"example": 0.62},
        description="纵坐标，相对于画布高度的比例，0 为最上，1 为最下",
    )


class SelectIn(BaseModel):
    """创建选区的请求参数（点选模式）。"""

    revision: int = Field(
        ...,
        json_schema_extra={"example": 5},
        description="当前画布修订号，用于防止选区过期",
    )
    points: list[PointIn] = Field(
        default=[],
        json_schema_extra={
            "example": [
                {"x": 0.3, "y": 0.4},
                {"x": 0.7, "y": 0.5},
            ],
        },
        description="标点列表，每个点标记一个物体的位置",
    )
    strokes: list[list[PointIn]] = Field(
        default=[],
        json_schema_extra={
            "example": [
                [{"x": 0.2, "y": 0.3}, {"x": 0.25, "y": 0.35}, {"x": 0.3, "y": 0.3}],
            ],
        },
        description="笔刷路径列表，每条路径是一组连续的点",
    )
    radius: float = Field(
        default=0.03, ge=0.005, le=0.12,
        json_schema_extra={"example": 0.05},
        description="笔刷半径，相对于画布宽度的比例，值越大选区越粗",
    )
    append: bool = Field(
        default=False,
        json_schema_extra={"example": False},
        description="是否追加到已有选区，false 则替换",
    )


class MarkerOut(BaseModel):
    """选区分点的返回信息。"""

    index: int = Field(json_schema_extra={"example": 0}, description="标点序号，从 0 开始")
    x: float = Field(json_schema_extra={"example": 0.35}, description="横坐标（相对比例）")
    y: float = Field(json_schema_extra={"example": 0.62}, description="纵坐标（相对比例）")


class SelectionOut(BaseModel):
    """选区结果返回。"""

    revision: int = Field(json_schema_extra={"example": 5}, description="选区对应的修订号")
    mask: AssetOut = Field(description="选区遮罩图片素材")
    markers: list[MarkerOut] = Field(
        default=[],
        json_schema_extra={
            "example": [
                {"index": 0, "x": 0.3, "y": 0.4},
                {"index": 1, "x": 0.7, "y": 0.5},
            ],
        },
        description="标点列表",
    )


class SessionOut(BaseModel):
    """会话列表项返回。"""

    id: uuid.UUID = Field(json_schema_extra={"example": "f1e2d3c4-b5a6-7890-abcd-ef1234567890"})
    title: str = Field(json_schema_extra={"example": "白色背景产品主图精修"})
    revision: int = Field(json_schema_extra={"example": 5}, description="当前修订号")
    history_seq: int = Field(json_schema_extra={"example": 12}, description="历史记录序号")
    original_asset_id: uuid.UUID = Field(
        json_schema_extra={"example": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"},
        description="最初的原始素材 ID",
    )
    current_asset_id: uuid.UUID = Field(
        json_schema_extra={"example": "d4e5f6a7-b8c9-0123-defa-234567890123"},
        description="当前使用的素材 ID",
    )
    created_at: datetime = Field(json_schema_extra={"example": "2026-08-26T10:30:00Z"})
    updated_at: datetime = Field(json_schema_extra={"example": "2026-08-26T14:22:30Z"})

    @classmethod
    def of(cls, record: EditSession) -> "SessionOut":
        return cls(
            id=record.id,
            title=record.title,
            revision=record.revision,
            history_seq=record.history_seq,
            original_asset_id=record.original_asset_id,
            current_asset_id=record.current_asset_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class SessionDetailOut(SessionOut):
    """会话详情返回，包含完整画布文档和图片墙。"""

    document: LayerDocument = Field(description="当前画布文档（图层数据）")
    previous_document: LayerDocument | None = Field(
        default=None,
        description="上一版本文档，用于对比预览",
    )
    assets: list[AssetOut] = Field(default=[], description="图片墙素材列表")
    can_undo: bool = Field(json_schema_extra={"example": True}, description="是否可以撤销")
    can_redo: bool = Field(json_schema_extra={"example": False}, description="是否可以重做")

    @classmethod
    def of_detail(
        cls,
        record: EditSession,
        assets: list[AssetOut],
        *,
        previous_document: LayerDocument | None = None,
        can_undo: bool = False,
        can_redo: bool = False,
    ) -> "SessionDetailOut":
        return cls(
            **SessionOut.of(record).model_dump(),
            document=LayerDocument.model_validate(record.document),
            previous_document=previous_document,
            assets=assets,
            can_undo=can_undo,
            can_redo=can_redo,
        )


class ToolInvokeOut(BaseModel):
    """工具调用结果返回。"""

    run: RunOut = Field(description="工具运行记录")
    session: SessionDetailOut = Field(description="调用后的会话详情")


class HistoryOut(BaseModel):
    """历史记录条目返回。"""

    seq: int = Field(json_schema_extra={"example": 12}, description="历史序号")
    action: str = Field(json_schema_extra={"example": "adjust_image"}, description="操作名称")
    params: dict = Field(
        json_schema_extra={"example": {"brightness": 10, "contrast": 5}},
        description="操作参数",
    )
    result: dict = Field(
        json_schema_extra={"example": {"status": "success"}},
        description="操作结果",
    )
    created_at: datetime = Field(json_schema_extra={"example": "2026-08-26T14:22:30Z"})

    @classmethod
    def of(cls, entry: EditHistory) -> "HistoryOut":
        return cls(
            seq=entry.seq,
            action=entry.action,
            params=entry.params,
            result=entry.result,
            created_at=entry.created_at,
        )
