"""
LLM 规划模型接入。

封装了规划模型（planner）的创建和工具绑定逻辑。
Agent 通过调用 planner() 获得一个已经绑定了所有编辑工具的 LLM 实例，
直接调用就能生成工具调用计划。

这是 Agent 的"大脑"接入层——把 Pydantic 定义的工具参数 Schema
转换成 OpenAI Function Calling 格式，再绑定到 LLM 上。
"""

from functools import lru_cache

from langchain_core.utils.function_calling import convert_to_openai_function
from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.tools import SPECS, ToolSpec

# 百炼兼容模式路径：纯文本模型可以走 OpenAI 兼容接口
# 为什么需要兼容模式？
# - 图像模型（文生图、图生图）必须走 DashScope 原生接口，因为 OpenAI 格式不支持图片输入
# - 纯文本的规划模型可以走 OpenAI 兼容模式，和 LangChain 生态无缝集成
# - 兼容模式的 base_url 比原生地址多了 /compatible-mode/v1 路径
_COMPATIBLE_PATH = "/compatible-mode/v1"


class PlannerUnavailable(Exception):
    """规划模型未配置或不可用时抛出，调用方需要捕获并给出友好提示。"""


def _schema_of(spec: ToolSpec) -> dict:
    """
    将 ToolSpec 转换为 OpenAI Function Calling 格式的工具定义。

    转换流程：
    1. 用 convert_to_openai_function 把 Pydantic 模型转成 JSON Schema
    2. 补上工具的 name 和 description（从 ToolSpec 中取）
    3. 移除 agent_hidden 中列出的字段（这些字段由服务端填入，不暴露给模型）
    4. 返回 {"type": "function", "function": {...}} 格式（OpenAI 工具调用的标准格式）

    为什么要移除 agent_hidden 字段？
    - 有些参数（如 mask_asset_id、revision）是服务端从上下文注入的
    - 如果暴露给模型，模型可能会乱填，导致执行失败
    - 只给模型看它需要决策的参数，减少出错概率
    """
    # 将 Pydantic 模型转换为 OpenAI function calling 格式
    function = convert_to_openai_function(spec.params)
    function["name"] = spec.name
    function["description"] = spec.description

    # 从参数 schema 中移除服务端注入的隐藏字段
    parameters = function.get("parameters", {})
    for hidden in spec.agent_hidden:
        parameters.get("properties", {}).pop(hidden, None)
    # 同时从 required 列表中移除，不然校验会说缺少必填字段
    parameters["required"] = [
        name for name in parameters.get("required", []) if name not in spec.agent_hidden
    ]
    return {"type": "function", "function": function}


@lru_cache
def planner():
    """
    获取绑定了全部工具的规划模型实例（单例）。

    做的事情：
    1. 读取配置，检查 API Key 是否配置
    2. 创建 ChatOpenAI 实例（通过兼容模式连接百炼）
    3. 将所有已注册的编辑工具转换成 Function Calling 格式
    4. 把工具绑定到 LLM 实例上
    5. 返回绑定后的 LLM 实例

    为什么用 lru_cache？
    - 模型实例和工具绑定只需要创建一次，每次调用都复用
    - 工具列表不会动态变化，缓存是安全的
    - 避免每次调用都重新创建模型实例和转换工具 Schema

    为什么用 ChatOpenAI 而不是专门的百炼 SDK？
    - 百炼提供了 OpenAI 兼容接口，LangChain 生态直接能用
    - 不需要额外学习新的 SDK API
    - 未来换其他 OpenAI 兼容服务商也很方便
    """
    settings = get_settings()
    if not settings.dashscope_api_key:
        raise PlannerUnavailable("未配置 DASHSCOPE_API_KEY，对话指令不可用")

    # 创建 ChatOpenAI 实例，通过兼容模式连接百炼
    # temperature=0：规划需要确定性输出，不需要随机性
    model = ChatOpenAI(
        model=settings.planner_model,
        api_key=settings.dashscope_api_key,
        base_url=f"{settings.dashscope_base_url}{_COMPATIBLE_PATH}",
        temperature=0,
    )
    # 把所有工具绑定到模型上
    # bind_tools 返回一个新的 ChatOpenAI 实例，调用时会自动带上工具定义
    return model.bind_tools([_schema_of(spec) for spec in SPECS])
