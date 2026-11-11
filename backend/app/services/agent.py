"""
Agent 业务编排服务。

负责 Agent 对话的完整业务流程：生成计划、确认、取消、重试、推进执行等。
这是 Agent 模块的"总指挥"——把 LLM 规划、工具执行、状态管理串起来。

核心流程：
1. 用户发消息 → 调用 LLM 生成计划 → 服务端校验 → 保存到数据库
2. 单步计划直接执行，多步计划等待用户确认
3. 用户确认后，按依赖关系逐步执行工具
4. 每步执行完后推进后续步骤，直到全部完成或失败
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app import agent
from app.agent import plan as plan_mod
from app.layers import Layer, LayerDocument, LayerKind
from app.models import AgentRun, EditSession, ToolRun
from app.models.tool_run import RunStatus
from app.services import assets, runs, selections, tools
from app.tools import spec_of

logger = logging.getLogger(__name__)


class TurnNotFound(Exception):
    """轮次不存在时抛出。"""


class CannotConfirm(Exception):
    """计划无法确认时抛出（状态不对等）。"""


class CannotCancel(Exception):
    """计划无法取消时抛出（已经结束了）。"""


class CannotRetry(Exception):
    """计划无法重试时抛出（没有失败的步骤）。"""


async def describe(session: AsyncSession, record: EditSession) -> str:
    """
    生成给规划模型的画布摘要文本，只给决策必需的事实。

    把画布状态浓缩成一段自然语言，作为系统提示词的一部分发给 LLM。
    包含的信息：
    - 画幅尺寸
    - 图层列表（自下而上的顺序，名字和类型）
    - 当前图的格式信息
    - 选区状态（有没有选区、是标点还是笔刷）
    - 修订号

    为什么需要单独生成摘要，而不是直接把整个 LayerDocument 发给模型？
    - 自然语言描述比 JSON 更省 Token（特别是图层多时）
    - 只给关键信息，减少模型被无关细节干扰
    - 明确告诉模型选区和图层的关系（正交维度）
    """
    # 从 JSON 字段解析出 LayerDocument 对象
    document = LayerDocument.model_validate(record.document)
    # 生成每个图层的描述文字
    names = [_layer_name(layer) for layer in document.layers]
    parts = [
        f"画幅 {document.width}×{document.height}",
        f"图层 {len(document.layers)} 个，自下而上（{'、'.join(names)}）",
        # 明确告诉模型 layer_id 的用法，避免模型乱用
        "layer_id 填图层 id 或名字；不填则作用在选区下最上层图像，换背景作用在背景层",
        f"修订号 {record.revision}",
    ]

    # 当前图片的格式信息
    current = await assets.get_for_user(session, record.user_id, record.current_asset_id)
    if current is not None:
        parts.append(f"当前图 {current.image_format}{'，含透明通道' if current.has_alpha else ''}")

    # 选区状态
    selected = await selections.get(record.id, record.revision)
    if selected:
        markers = selected.get("markers") or []
        where = f"已有选区，{len(markers)} 个标点" if markers else "已有笔刷选区"
        # 强调选区和图层是正交的，避免模型混淆
        parts.append(f"{where}（选区只限定区域，不限定图层；与 layer_id 同时给出时取交集）")
    else:
        parts.append("当前无选区，区域工具会作用在整个图层")
    return "；".join(parts)


def _layer_name(layer: Layer) -> str:
    """
    生成图层的可读名称，用于画布摘要。

    文字图层会显示文字内容（截断到 8 字），隐藏图层加「·隐藏」标记。
    格式：图层id=名称 或 图层id=文字「内容」
    """
    text = f"文字「{layer.text[:8]}」" if layer.kind is LayerKind.TEXT and layer.text else None
    name = f"{layer.id}={text or layer.name}"
    return name if layer.visible else f"{name}·隐藏"


async def respond(session: AsyncSession, record: EditSession, goal: str) -> AgentRun:
    """
    处理一轮用户消息：规划 → 校验 → 落库 → （单步直接执行）。

    执行流程：
    1. 调用 agent.run() 生成计划（内部走 LangGraph：plan → verify）
    2. 把当前选区注入到计划步骤中
    3. 处理异常（规划不可用、未知错误）
    4. 根据计划情况设置状态：
       - 有错误 → FAILED
       - 多步需要确认 → QUEUED
       - 单步直接执行 → RUNNING
       - 没有计划（模型拒绝） → SUCCEEDED
    5. 保存 AgentRun 到数据库
    6. 如果是 RUNNING 状态，立即推进执行

    为什么多步要等确认？
    - 多步操作改动大，让用户检查一下计划是否合理
    - 避免模型理解错误导致一堆误操作
    - 单步操作影响小，直接执行效率更高
    """
    revision = record.revision
    reply, steps, error = "", [], None

    try:
        # 调用 Agent 生成计划
        reply, steps = await agent.run(goal, await describe(session, record))
        # 把选区信息注入到计划步骤中
        steps = await _pin_selection(session, record, steps)
    except agent.PlannerUnavailable as exc:
        # 规划模型没配置，友好提示
        error = str(exc)
    except Exception:
        # 其他异常，记录日志并返回通用错误
        logger.exception("指令规划异常 session_id=%s", record.id)
        await session.rollback()
        error = "规划失败，请重试"

    # 确定整轮计划的初始状态
    if error:
        status = RunStatus.FAILED
    elif plan_mod.needs_confirm(steps):
        # 多步计划，等用户确认
        status = RunStatus.QUEUED
    elif steps:
        # 单步计划，直接开跑
        status = RunStatus.RUNNING
    else:
        # 没有计划（模型说做不到），也算成功结束（只是没做事）
        status = RunStatus.SUCCEEDED

    # 保存到数据库
    turn = AgentRun(
        user_id=record.user_id,
        session_id=record.id,
        revision=revision,
        goal=goal,
        reply=reply,
        plan=steps,
        status=status,
        error=error,
    )
    session.add(turn)
    await session.commit()
    await session.refresh(turn)

    # 如果是直接执行的状态，立即推进第一步
    if turn.status is RunStatus.RUNNING:
        await _advance(session, turn)
    return turn


async def _pin_selection(
    session: AsyncSession, record: EditSession, steps: list[dict]
) -> list[dict]:
    """
    把当前选区钉进每个需要遮罩的步骤。

    为什么要提前注入选区？
    - 选区信息模型不知道（mask_asset_id 在 agent_hidden 里）
    - 第一步执行后选区可能会被清除或 revision 会变
    - 显式把选区 id 写入每一步，确保后续步骤都用同一块区域
    - 不然第一步用了选区，第二步就找不到选区了
    """
    stored = await selections.get(record.id, record.revision)
    mask_asset_id = (stored or {}).get("mask_asset_id")
    if not mask_asset_id:
        return steps
    # 遍历每个步骤，需要遮罩的工具就注入 mask_asset_id
    for step in steps:
        params = step["params"]
        if _wants_mask(step["tool"]) and not params.get("mask_asset_id"):
            step["params"] = {**params, "mask_asset_id": mask_asset_id}
    return steps


def _wants_mask(tool: str) -> bool:
    """判断一个工具是否需要遮罩参数（通过检查 params 里有没有 mask_asset_id 字段）。"""
    return "mask_asset_id" in spec_of(tool).params.model_fields


async def confirm(session: AsyncSession, record: EditSession, turn_id: uuid.UUID) -> AgentRun:
    """
    用户确认计划：将等待确认的步骤标记为已批准，开始执行。

    执行流程：
    1. 查询轮次记录，确认状态是 QUEUED
    2. 将所有 WAITING 状态的步骤改为 PENDING，并标记 approved=True
    3. 更新状态为 RUNNING
    4. 调用 _advance 开始执行

    为什么需要 approved 标记？
    - 有些工具需要用户确认才能执行（needs_approval=True）
    - 这些工具即使依赖满足了，也会停在 WAITING 状态等确认
    - 用户确认后，approved=True，才能继续执行
    """
    turn = await _get(session, record, turn_id)
    steps = _copy(turn.plan)
    # 找出所有在等待确认的步骤
    waiting = [step for step in steps if step["status"] == plan_mod.WAITING]
    if turn.status is not RunStatus.QUEUED and not waiting:
        raise CannotConfirm
    # 标记为已批准，状态改成 pending（可以被执行了）
    for step in waiting:
        step["status"] = plan_mod.PENDING
        step["approved"] = True
    turn.plan = steps
    turn.status = RunStatus.RUNNING
    _touch(turn)
    await session.commit()
    return await _advance(session, turn)


async def cancel(session: AsyncSession, record: EditSession, turn_id: uuid.UUID) -> AgentRun:
    """
    取消计划：取消所有未执行的步骤。

    执行流程：
    1. 查询轮次记录，确认还没结束
    2. 把 pending 和 waiting 状态的步骤标记为 canceled
    3. 取消已经投递到队列但还没开始执行的任务
    4. 重新计算整体状态
    5. 保存并返回
    """
    turn = await _get(session, record, turn_id)
    if turn.status.is_terminal:
        raise CannotCancel
    # 标记未执行的步骤为已取消
    turn.plan = plan_mod.cancel_remaining(_copy(turn.plan))
    # 取消已经在队列里的任务（还没开始跑的）
    await _cancel_queued_runs(session, turn.plan)
    _touch(turn)
    # 重新计算整体状态
    turn.status = RunStatus(plan_mod.settle(turn.plan))
    await session.commit()
    await session.refresh(turn)
    return turn


async def retry(session: AsyncSession, record: EditSession, turn_id: uuid.UUID) -> AgentRun:
    """
    重试失败的步骤：找到最后一个失败的步骤，重置状态后重新执行。

    为什么只重试最后一个失败的？
    - 按顺序执行，后面的步骤依赖前面的，前面失败了后面不会执行
    - 从最后一个失败的地方重试，不需要从头来
    - 用户体验更好，已经成功的步骤不会重新执行
    """
    turn = await _get(session, record, turn_id)
    steps = _copy(turn.plan)
    # 从后往前找第一个失败的步骤
    failed = next((step for step in reversed(steps) if step["status"] == plan_mod.FAILED), None)
    if failed is None:
        raise CannotRetry
    # 重置失败步骤的状态，准备重新执行
    failed["run_id"] = None
    failed["status"] = plan_mod.PENDING
    turn.plan = steps
    turn.status = RunStatus.RUNNING
    turn.error = None
    _touch(turn)
    await session.commit()
    return await _advance(session, turn)


async def continue_plan(session: AsyncSession, run: ToolRun) -> None:
    """
    某一步执行完成后，推进计划的后续步骤。

    这是 Worker 完成工具执行后的回调入口——Worker 执行完一步，
    调用这个函数更新步骤状态，并推进后续依赖步骤。

    为什么叫 continue_plan 而不是 on_step_complete？
    - 它不仅处理完成，也处理失败（状态同步过去就行）
    - 它的核心动作是"推进"，不是"响应完成"
    - 刷新后也能从已落库的计划接着跑（幂等）
    """
    if run.session_id is None:
        return
    # 找到这个 run 属于哪个 turn
    turn = await _turn_of_run(session, run)
    if turn is None:
        return

    # 更新对应步骤的状态
    steps = _copy(turn.plan)
    for step in steps:
        if step.get("run_id") == str(run.id):
            step["status"] = run.status.value
    turn.plan = steps
    _touch(turn)
    # 推进下一步。注意这里先不落库：
    # 如果下一步是同步执行的（如翻转），会和这一步一起提交事务，
    # 前端不会看到「上一步完了、下一步还没开始」的中间状态
    await _advance(session, turn)


async def turns_of(session: AsyncSession, record: EditSession, limit: int = 50) -> list[AgentRun]:
    """获取会话的所有轮次记录，按创建时间排序。"""
    result = await session.scalars(
        select(AgentRun)
        .where(AgentRun.session_id == record.id)
        .order_by(AgentRun.created_at)
        .limit(limit)
    )
    return list(result)


async def _advance(session: AsyncSession, turn: AgentRun) -> AgentRun:
    """
    推进计划执行：找出所有可以执行的步骤，逐个提交。

    执行逻辑：
    1. 循环：找出所有 ready 的步骤
    2. 逐个提交：
       - 需要确认但还没批准的 → 设为 WAITING，跳过
       - 提交执行 → 更新步骤状态和 run_id
       - 每提交一步就 commit 一次（让前端能看到进度）
    3. 如果某一步是同步完成的（succeeded），标记 progressed=True，继续下一轮循环
       （因为这步完成了，可能有新的步骤变成 ready）
    4. 直到没有新的 ready 步骤，退出循环
    5. 最后更新整体状态

    为什么用 while 循环而不是一次性提交所有 ready 的？
    - 同步工具（queued=False）会立即完成，完成后可能解锁新的步骤
    - 一轮循环可能触发多步同步执行
    - 异步工具提交后状态是 queued，不会立即完成，所以不会继续推进

    为什么每步都 commit？
    - 让前端能实时看到每一步的状态变化
    - 如果某一步失败了，前面的步骤已经落库，不会全部回滚
    - 每步都是独立的事务边界
    """
    steps = _copy(turn.plan)
    progressed = True
    while progressed:
        progressed = False
        # 遍历所有可以执行的步骤
        for step in plan_mod.ready(steps):
            # 工具需要确认但还没批准 → 停在 WAITING 等用户确认
            if spec_of(step["tool"]).needs_approval and not step.get("approved"):
                step["status"] = plan_mod.WAITING
                continue
            # 提交工具执行
            run = await tools.submit(
                session, turn.user_id, step["tool"], step["params"], turn.session_id
            )
            step["run_id"] = str(run.id)
            step["status"] = run.status.value
            turn.plan = steps
            turn.status = RunStatus.RUNNING
            _touch(turn)
            await session.commit()
            # 如果是同步工具（立即完成），标记 progressed 继续循环
            # 异步工具（queued）提交后状态是 queued，不会立即完成
            progressed = run.status is RunStatus.SUCCEEDED

    # 所有能推进的都推进完了，更新整体状态
    turn.plan = steps
    turn.status = RunStatus(plan_mod.settle(steps))
    # 如果有步骤在等确认，加一句提示
    waiting = any(step["status"] == plan_mod.WAITING for step in steps)
    if turn.status is RunStatus.QUEUED and waiting:
        turn.reply = turn.reply or "下一步需要确认后再执行。"
    _touch(turn)
    await session.commit()
    await session.refresh(turn)
    return turn


async def _cancel_queued_runs(session: AsyncSession, steps: list[dict]) -> None:
    """
    取消已经投递到队列但还没开始执行的任务。

    用户取消计划时，已经在队列里的任务也需要取消，
    不然 Worker 还是会把它们执行了。
    """
    for step in steps:
        if not step.get("run_id") or step["status"] != plan_mod.QUEUED:
            continue
        run = await session.get(ToolRun, uuid.UUID(step["run_id"]))
        if run is None or run.status is not RunStatus.QUEUED:
            continue
        # 标记为已取消（ARQ 那边也会检测状态，不执行已取消的任务）
        await runs.finish(session, run, status=RunStatus.CANCELED, error="已取消")
        step["status"] = plan_mod.CANCELED


async def _get(session: AsyncSession, record: EditSession, turn_id: uuid.UUID) -> AgentRun:
    """查询指定轮次，确保属于当前会话。不存在则抛 TurnNotFound。"""
    turn = await session.scalar(
        select(AgentRun).where(AgentRun.id == turn_id, AgentRun.session_id == record.id)
    )
    if turn is None:
        raise TurnNotFound
    return turn


async def _turn_of_run(session: AsyncSession, run: ToolRun) -> AgentRun | None:
    """
    根据 ToolRun 找到它属于哪个 AgentRun。

    因为 plan 是 JSON 字段存的，没法直接 JOIN 查询，
    所以查最近 20 个 turn，遍历 plan 找有没有匹配的 run_id。

    为什么只查最近 20 个？
    - 通常一个 run 肯定属于最近的几个 turn
    - 查太多效率低，20 个足够覆盖了
    - 真的找不到就算了，不影响功能（只是不推进后续步骤而已）
    """
    turns = await session.scalars(
        select(AgentRun)
        .where(AgentRun.session_id == run.session_id)
        .order_by(AgentRun.created_at.desc())
        .limit(20)
    )
    run_id = str(run.id)
    for turn in turns:
        if any(step.get("run_id") == run_id for step in turn.plan):
            return turn
    return None


def _copy(steps: list) -> list[dict]:
    """
    浅拷贝步骤列表。

    为什么需要拷贝？
    - SQLAlchemy 的 JSON 字段修改内部结构不会自动检测到变更
    - 我们需要先复制一份，修改完再整体赋值回去
    - 浅拷贝就够了，因为步骤里的 value 都是基本类型
    """
    return [dict(step) for step in steps]


def _touch(turn: AgentRun) -> None:
    """
    标记 plan 字段已修改。

    SQLAlchemy 检测不到 JSON 字段内部的变更（如 step["status"] = xxx），
    需要手动调用 flag_modified 告诉它这个字段变了，不然 commit 不会更新。
    """
    flag_modified(turn, "plan")
