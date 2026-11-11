"""
Agent 计划校验与状态管理。

负责验证模型输出的计划是否合法、管理计划步骤的状态流转、
判断哪些步骤可以执行、汇总整轮计划的状态。

这是 Agent 的"质量控制"层——模型生成的计划不能直接执行，
必须经过服务端校验，确保工具存在、参数合法、没有循环依赖等问题。

核心概念：
- 计划（plan）：一组按依赖关系排列的步骤
- 步骤（step）：一次工具调用，有自己的状态和依赖
- 状态机：pending → waiting → queued → running → succeeded/failed/canceled
"""

from app.services import tools as tool_service
from app.tools import spec_of

# 单轮计划最多允许的步数，防止模型输出过长的计划
MAX_STEPS = 8

# 步骤状态常量
PENDING = "pending"      # 初始状态，还没排队
WAITING = "waiting"      # 等待依赖完成
QUEUED = "queued"        # 已投递到队列，等待执行
RUNNING = "running"      # 正在执行
SUCCEEDED = "succeeded"  # 执行成功
FAILED = "failed"       # 执行失败
CANCELED = "canceled"    # 已取消

# 进行中的状态（还没结束）
_OPEN = {PENDING, WAITING, QUEUED, RUNNING}
# 阻塞状态（依赖这些状态的步骤不能执行）
_BLOCKED = {FAILED, CANCELED}


class PlanError(Exception):
    """计划校验失败时抛出，如步数超限、循环依赖、依赖不存在等。"""


def assemble(raw: list[dict]) -> list[dict]:
    """
    将模型输出的原始计划组装成标准格式，并校验合法性。

    做的事情：
    1. 检查步数是否超限（最多 8 步）
    2. 给每个步骤补全 id（模型没给的话自动生成 s1, s2...）
    3. 补全依赖关系（没写 depends_on 的默认依赖上一步）
    4. 初始化状态为 pending
    5. 检查所有依赖的步骤是否存在
    6. 检查是否有循环依赖

    Args:
        raw: 模型输出的原始步骤列表，每个元素至少有 tool 字段

    Returns:
        组装后的步骤列表，每个步骤包含 id/tool/params/depends_on/status 等字段

    Raises:
        PlanError: 步数超限、依赖不存在、循环依赖

    为什么默认依赖上一步？
    - 模型通常按顺序输出步骤，后一步依赖前一步是最常见的情况
    - 减少模型需要输出的内容，降低出错概率
    - 如果模型明确写了 depends_on，就用模型自己的依赖关系
    """
    if len(raw) > MAX_STEPS:
        raise PlanError(f"计划超过 {MAX_STEPS} 步，请拆成多次说明")

    steps: list[dict] = []
    for index, item in enumerate(raw, start=1):
        # 步骤 ID：模型给了就用，没给就自动生成 s1, s2...
        step_id = item.get("id") or f"s{index}"
        # 依赖关系：没写的话默认依赖上一步（第一步没有依赖）
        depends = item.get("depends_on")
        if depends is None:
            depends = [steps[-1]["id"]] if steps else []
        steps.append(
            {
                "id": step_id,
                "tool": item["tool"],
                "params": item.get("params") or {},
                "depends_on": list(depends),
                "run_id": item.get("run_id"),
                "status": item.get("status") or PENDING,
            }
        )

    # 检查所有依赖的步骤是否存在
    known = {step["id"] for step in steps}
    for step in steps:
        missing = [dep for dep in step["depends_on"] if dep not in known]
        if missing:
            raise PlanError(f"步骤 {step['id']} 依赖了不存在的步骤")
    # 检查是否有循环依赖
    if _cyclic(steps):
        raise PlanError("计划步骤存在循环依赖")
    return steps


def validate(raw: list[dict]) -> list[dict]:
    """
    完整校验计划：工具存在性 + 参数合法性 + 结构合法性。

    执行流程：
    1. 遍历每个步骤，通过工具名找到 ToolSpec
    2. 用 tool_service.validate 校验参数是否符合 Schema
    3. 将校验后的参数替换回步骤（Pydantic 可能会做类型转换和默认值填充）
    4. 调用 assemble 进行结构校验（依赖、循环等）

    为什么不在 assemble 里一起做参数校验？
    - 职责分离：assemble 只关心计划的结构（步骤、依赖、状态）
    - validate 关心内容（工具是否存在、参数是否合法）
    - 有些场景只需要结构校验，不需要参数校验（比如从数据库加载历史计划）
    """
    checked: list[dict] = []
    for step in raw:
        # 通过工具名查找 ToolSpec，找不到会抛 UnknownTool
        spec = spec_of(step["tool"])
        # 校验参数是否符合工具的 Pydantic Schema
        params = tool_service.validate(spec.name, step.get("params") or {})
        checked.append({**step, "tool": spec.name, "params": params})
    return assemble(checked)


def ready(steps: list[dict]) -> list[dict]:
    """
    返回当前可以执行的步骤（依赖都已完成且自身未执行的）。

    判断条件：
    - 状态是 pending 或 waiting（还没开始执行）
    - 没有 run_id（还没投递过）
    - 所有依赖的步骤都已成功完成
    - 没有依赖处于阻塞状态（失败/取消）

    为什么需要这个函数？
    - 计划执行器通过它知道下一步该跑哪些步骤
    - 支持并行：没有依赖关系的步骤可以同时执行
    - 拓扑执行的核心逻辑
    """
    by_id = {step["id"]: step for step in steps}
    return [step for step in steps if _is_ready(step, by_id)]


def settle(steps: list[dict]) -> str:
    """
    根据所有步骤的状态，汇总出整轮计划的整体状态。

    汇总规则：
    - 有任何步骤在进行中（open）→ running
      - 但如果只有 waiting 没有 queued/running → queued（全部在等待依赖）
    - 有任何步骤失败 → failed
    - 有任何步骤取消 → canceled
    - 全部成功 → succeeded

    为什么需要区分 running 和 queued？
    - 前端展示不同的状态提示
    - queued 表示计划已确认但还在等前面的步骤，没有真正在跑
    - running 表示至少有一步在执行中
    """
    statuses = [step["status"] for step in steps]
    if any(status in _OPEN for status in statuses):
        # 全部在 waiting 状态，没有正在执行或排队的 → 整体是 queued
        if any(status == WAITING for status in statuses) and not any(
            status in {QUEUED, RUNNING} for status in statuses
        ):
            return QUEUED
        return RUNNING
    if any(status == FAILED for status in statuses):
        return FAILED
    if any(status == CANCELED for status in statuses):
        return CANCELED
    return SUCCEEDED


def needs_confirm(steps: list[dict]) -> bool:
    """
    判断计划是否需要用户确认。

    规则：多步计划需要确认，单步直接执行。
    为什么多步需要确认？
    - 多步操作可能会产生较大的改动，让用户确认一下更安全
    - 单步操作影响小，直接执行效率更高
    - 也给用户一个检查计划是否正确的机会
    """
    return len(steps) > 1


def cancel_remaining(steps: list[dict]) -> list[dict]:
    """
    取消计划中尚未执行的步骤。

    将 pending 和 waiting 状态的步骤标记为 canceled，
    已经在执行或已经结束的步骤保持不变。

    为什么不直接删除这些步骤？
    - 保留历史记录：用户可以看到完整的计划，包括被取消的部分
    - 便于调试和分析：知道哪些步骤被取消了，为什么
    - 状态流转更清晰
    """
    return [
        {**step, "status": CANCELED} if step["status"] in {PENDING, WAITING} else step
        for step in steps
    ]


def _is_ready(step: dict, by_id: dict[str, dict]) -> bool:
    """判断单个步骤是否可以执行。"""
    # 已经在执行或完成了，不能再执行
    if step["status"] not in {PENDING, WAITING} or step.get("run_id"):
        return False
    # 找出所有依赖的步骤
    deps = [by_id[dep] for dep in step["depends_on"]]
    # 有任何依赖被阻塞（失败/取消），这步也不能执行
    if any(dep["status"] in _BLOCKED for dep in deps):
        return False
    # 所有依赖都成功了，才能执行
    return all(dep["status"] == SUCCEEDED for dep in deps)


def _cyclic(steps: list[dict]) -> bool:
    """
    检测计划步骤是否存在循环依赖。

    使用 Kahn 拓扑排序算法：
    1. 统计每个节点的入度（有多少个依赖）
    2. 将入度为 0 的节点加入队列
    3. 依次从队列取出节点，减少其邻居的入度
    4. 邻居入度变为 0 时加入队列
    5. 最后比较访问过的节点数和总节点数
       - 相等 → 无环
       - 不等 → 有环（环中的节点入度永远不会变成 0）

    时间复杂度 O(V+E)，V 是步骤数，E 是依赖数。

    为什么用 Kahn 而不是 DFS？
    - 顺便可以做拓扑排序（虽然这里只用来检测环）
    - 迭代实现，不会有递归深度问题
    - 结果稳定，便于调试
    """
    ids = [step["id"] for step in steps]
    # incoming: 每个节点的入度（有多少个依赖指向它）
    incoming = {step_id: 0 for step_id in ids}
    # outgoing: 每个节点指向哪些节点（反向依赖关系）
    outgoing: dict[str, list[str]] = {step_id: [] for step_id in ids}
    for step in steps:
        for dep in step["depends_on"]:
            # dep 是被依赖的节点，dep → step 是一条边
            outgoing[dep].append(step["id"])
            incoming[step["id"]] += 1

    # 从入度为 0 的节点开始（没有任何依赖的步骤）
    queue = [step_id for step_id, count in incoming.items() if count == 0]
    seen = 0
    while queue:
        node = queue.pop()
        seen += 1
        # 遍历当前节点的所有邻居
        for nxt in outgoing[node]:
            incoming[nxt] -= 1
            # 邻居的入度变为 0，说明它的所有依赖都完成了
            if incoming[nxt] == 0:
                queue.append(nxt)
    # 如果访问过的节点数不等于总节点数，说明有环
    # 环中的节点入度永远不会变成 0，所以不会被访问到
    return seen != len(ids)
