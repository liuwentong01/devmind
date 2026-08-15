"""通用编排引擎：构建最小 ReAct Agent（模型 → 工具 → 观察循环）。

架构位置：通用聊天路由使用本图回答无需垂直业务状态机的问题。它把统一 LLM 网关与
:mod:`app.kernel.tools.builtin` 的受限工具绑定给 LangGraph 预置 ReAct 图；知识问答、RCA、
代码审查等有明确业务状态的流程则在 :mod:`app.agents` 自行定义图。

典型调用示例::

    result = await build_agent().ainvoke(
        {"messages": [("user", "上海现在几点？再计算 12 / 3")]},
        config={"recursion_limit": 8},
    )

构建图本身没有外部副作用，执行时会调用 LLM，模型也可能选择时间/计算工具。上层必须设置
递归、总时长和 SSE 发送边界，防止模型反复调用工具。当前工具是只读且本地受限的；这里
未实现动态 MCP 工具、每工具审批、长期记忆或任务路由，不应直接挂载有破坏性的工具。
"""

from functools import lru_cache

from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent

from app.core.logging import get_logger
from app.core.prompt_security import USER_INPUT_POLICY
from app.kernel.llm.gateway import get_chat_model
from app.kernel.tools.builtin import get_builtin_tools

logger = get_logger("orchestrator")

SYSTEM_PROMPT = (
    USER_INPUT_POLICY + "\n\n你是 DevMind 研发效能智能体平台的助手。"
    "用简洁、专业的中文回答。"
    "当需要当前时间或做数学计算时，调用相应工具，不要凭空臆造。"
)


@lru_cache(maxsize=1)
def build_agent() -> CompiledStateGraph:
    """构建并缓存最小 ReAct 图。

    Returns:
        可接受 LangGraph ``messages`` 状态的编译图。

    缓存的是无会话状态的图拓扑和工具绑定；对话消息始终由调用方逐次传入。本函数没有
    checkpointer，因此当前不会在不同请求之间自动保留对话记忆。
    """
    model = get_chat_model()
    tools = get_builtin_tools()
    logger.info("agent_build", tools=[t.name for t in tools])

    return create_react_agent(
        model=model,
        tools=tools,
        prompt=SYSTEM_PROMPT,
    )
