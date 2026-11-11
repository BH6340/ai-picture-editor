"""
LangGraph Agent 图定义。

定义了规划 Agent 的状态图（StateGraph）：
接收用户指令和画布上下文 → 调用 LLM 生成计划 → 服务端校验计划 → 返回结果。

这是 Agent 的"骨架"——用 LangGraph 把 LLM 调用和校验逻辑
组织成一个有向图，清晰地定义了数据流向和处理步骤。

为什么用 LangGraph 而不是直接顺序调用？
- 状态管理：AgentState 统一管理所有状态，节点之间通过状态传递数据
- 可扩展：未来加循环（如计划不行就重试）、分支（如用户确认）都很方便
- 生态兼容：LangChain 的工具调用、输出解析等能力直接复用
- 可视化：可以导出图结构，便于调试和理解
"""

from functools import lru_cache
from typing import TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app.agent.llm import planner
from app.agent.plan import PlanError, validate
from app.services import tools as tool_service
from app.tools import UnknownTool, label_of

# 系统提示词：告诉模型它的角色、可用工具、使用规则
# 这是 Agent 的"人格设定"，直接影响模型输出的质量和格式
_SYSTEM = """你是电商图片修图助手，通过调用工具完成用户的修图请求。

规则：
- 只能使用已提供的工具。可以一次安排多步，按完成先后调用；后一步默认依赖前一步。
- 能一步完成的不要拆成多步，最多 8 步。
- 缺失参数用画布信息与常识补齐，可推断的参数不要反问用户。
- 图层与选区是两个独立维度：layer_id 决定改哪一层，选区决定改哪块区域。
  用户点名了图层（如「物体2」「背景」「主体」）就把它的 id 或名字填进 layer_id。
- 画布摘要标明已有选区时，那就是用户要改的物体：局部消除、替换、提升为图层
  直接调用，不要再让用户重选，也不要猜测选区是否点准。没有选区也能调用，
  此时作用在整个目标图层。
- 一句话要改多个图层时，按图层拆成多步，每步只填一个 layer_id。
- 改颜色、换材质、换成某物用 replace_region 并点名 layer_id；
  只改整张背景才用 replace_background。adjust_image 只做明暗冷暖，不做改色。
- 翻转画面用 flip_layer；说了角度用 rotate_layer。
- 用户要求拆层、把物体独立成层时，使用 split_layers 或 promote_object_to_layer。
- 拆层默认不拆文字，只有用户明确要求时才传 include_text。
- 改文字图层的文案、字号或颜色用 set_layer_text，并点名图层。
- 出营销图、主图、场景图、模特图、海报用 generate_marketing，
  kind 分别为 product / scene / model / poster。结果只进图片墙。
- 改尺寸、出投放物料、出 1:1 / 4:5 / 9:16 用 prepare_delivery_sizes，
  不要裁切主体，也不要用 crop_canvas。
- 对多张图做同一套去背景、换背景、调色、超分、扩图或改尺寸时用 batch_process。
  单张精修、营销图、局部编辑和拆层不要用它。
- 调用工具时不要输出解释或工具名。只有指令与修图无关、或现有工具确实做不到时，
  才用一句中文说明，不要提内部参数。

当前画布：{context}"""

# 模型说"我做不到"时的兜底回复
_FALLBACK_REPLY = "没太理解这条指令，换个说法或说得更具体一些。"
# 拒绝类回复的最大长度（防止模型输出一大段解释）
_REFUSAL_LIMIT = 60


class AgentState(TypedDict):
    """
    Agent 图的状态定义，在各个节点之间传递。

    字段说明：
    - goal: 用户的原始指令（自然语言）
    - context: 画布上下文摘要（图层信息、选区状态等）
    - plan: 生成的计划步骤列表，每步包含 tool 和 params
    - reply: 模型的自然语言回复（说给用户听的话）

    为什么用 TypedDict 而不是 dataclass / Pydantic？
    - LangGraph 原生支持 TypedDict 作为状态类型
    - 轻量：不需要额外的序列化逻辑
    - 状态是纯数据，不需要方法，TypedDict 刚好
    - 类型检查时能提供字段提示
    """

    goal: str
    context: str
    plan: list[dict]
    reply: str


async def _plan(state: AgentState) -> AgentState:
    """
    规划节点：调用 LLM 生成工具调用计划。

    做的事情：
    1. 构造消息列表：系统提示词（含画布上下文） + 用户指令
    2. 调用绑定了工具的 LLM
    3. 解析模型返回的 tool_calls，转换成标准的计划格式
    4. 提取模型的文本回复（如果有的话）

    返回的是状态的更新（部分字段），LangGraph 会自动合并到总状态中。
    """
    message = await planner().ainvoke(
        [
            SystemMessage(_SYSTEM.format(context=state["context"])),
            HumanMessage(state["goal"]),
        ]
    )
    return {
        # 将模型的工具调用转换成我们的计划格式：[{tool, params}, ...]
        "plan": [{"tool": call["name"], "params": call["args"]} for call in message.tool_calls],
        "reply": _text_of(message),
    }


def _verify(state: AgentState) -> AgentState:
    """
    校验节点：对模型生成的计划进行服务端校验。

    校验内容：
    - 工具是否存在
    - 参数是否符合 Schema
    - 依赖关系是否合法
    - 是否有循环依赖

    校验失败时，不抛出异常，而是把计划清空，回复设为错误信息。
    为什么不直接抛异常？
    - LangGraph 的节点抛异常会导致整个图执行失败
    - 我们希望即使计划有问题，也能给用户一个友好的提示
    - 把错误当成正常的状态流转，而不是异常
    """
    try:
        return {"plan": validate(state["plan"])}
    except (UnknownTool, tool_service.InvalidParams, PlanError) as exc:
        return {"plan": [], "reply": f"这一步暂时执行不了：{exc}"}


@lru_cache
def _graph():
    """
    创建并编译 Agent 状态图（单例，用 lru_cache 缓存）。

    图结构：
    START → plan → verify → END

    两个节点：
    - plan: 调用 LLM 生成计划
    - verify: 服务端校验计划合法性

    为什么是线性的？当前的规划流程是"生成 → 校验 → 返回"，
    没有循环或分支。但用 StateGraph 而不是直接顺序调用，是为了：
    - 未来扩展方便（比如加重试循环、用户确认节点等）
    - 状态管理统一
    - 可以利用 LangGraph 的 checkpoint、持久化等特性

    为什么用 lru_cache？
    - 图的结构是固定的，只需要构建一次
    - 每次调用都重新构建是浪费
    - 注意：这是普通函数的 lru_cache，不是异步的，因为构建图本身是同步操作
    """
    builder = StateGraph(AgentState)
    # 添加节点
    builder.add_node("plan", _plan)
    builder.add_node("verify", _verify)

    # 添加边（数据流方向）
    builder.add_edge(START, "plan")
    builder.add_edge("plan", "verify")
    builder.add_edge("verify", END)
    return builder.compile()


async def run(goal: str, context: str) -> tuple[str, list[dict]]:
    """
    执行一轮规划：生成计划并校验，返回答复文本和计划步骤。

    这是 Agent 对外的主入口，service 层调用这个函数来获取规划结果。

    Args:
        goal: 用户的自然语言指令
        context: 画布上下文摘要（图层、选区等信息）

    Returns:
        (reply, plan): 自然语言回复 + 计划步骤列表
        plan 可能是空列表（模型拒绝或校验失败）
    """
    state = await _graph().ainvoke({"goal": goal, "context": context, "plan": [], "reply": ""})
    return spoken(state["reply"], state["plan"]), state["plan"]


def spoken(reply: str, plan: list[dict]) -> str:
    """
    根据计划情况生成最终要说给用户听的话。

    规则：
    - 有计划且模型有回复 → 用模型的回复
    - 有计划但模型没说话 → 自动生成执行提示（多步列出工具名，单步说"正在xxx"）
    - 没计划 → 用兜底回复或截成一句话的拒绝

    为什么需要这个函数？
    - 模型有时候只调用工具不说人话，需要补一句自然语言
    - 模型说的话可能太长或不对，需要做后处理
    - 统一话术格式，保持体验一致
    """
    text = (reply or "").strip()
    if plan:
        if text and text != _FALLBACK_REPLY:
            return text
        # 模型没说话，自动生成提示
        labels = "、".join(label_of(step["tool"], step.get("params")) for step in plan)
        if len(plan) > 1:
            return f"将按以下步骤执行：{labels}。确认后开始。"
        return f"好，正在{labels}。"
    # 没有计划，返回兜底或截断的拒绝
    return _one_sentence(text) if text else _FALLBACK_REPLY


def _one_sentence(text: str) -> str:
    """
    将模型的拒绝回复截成一句话。

    模型拒绝时有时候会说一大堆解释，太长了体验不好。
    取第一句话就够了，长了就截断加省略号。
    """
    for sep in ("。", "！", "？", "\n"):
        head, found, _ = text.partition(sep)
        if found and head.strip():
            return head.strip() + (sep if sep != "\n" else "。")
    if len(text) > _REFUSAL_LIMIT:
        return text[:_REFUSAL_LIMIT].rstrip() + "…"
    return text


def _text_of(message: AIMessage) -> str:
    """
    从 AIMessage 中提取纯文本内容。

    AIMessage.content 可能是字符串，也可能是内容块列表（多模态时）。
    这里统一处理，返回纯文本字符串。
    """
    if isinstance(message.content, str):
        return message.content.strip()
    return "".join(
        block.get("text", "") for block in message.content if isinstance(block, dict)
    ).strip()
