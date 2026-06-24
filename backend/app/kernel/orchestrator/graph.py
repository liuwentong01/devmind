"""编排引擎：用 LangGraph 构建最小可用的 ReAct Agent（思考→工具→观察 循环）。

P0 用 langgraph 预置的 create_react_agent 跑通「LLM + 工具 + 循环」，
后续每个垂直 Agent 会在此基础上自定义节点与状态机。
"""

from functools import lru_cache

from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent

from app.core.logging import get_logger
from app.kernel.llm.gateway import get_chat_model
from app.kernel.tools.builtin import get_builtin_tools

logger = get_logger("orchestrator")

SYSTEM_PROMPT = (
    "你是 DevMind 研发效能智能体平台的助手。"
    "用简洁、专业的中文回答。"
    "当需要当前时间或做数学计算时，调用相应工具，不要凭空臆造。"
)


@lru_cache(maxsize=1)
def build_agent() -> CompiledStateGraph:
    """构建并编译最小 ReAct Agent（带缓存，进程内复用）。"""
    model = get_chat_model()
    tools = get_builtin_tools()
    logger.info("agent_build", tools=[t.name for t in tools])

    return create_react_agent(
        model=model,
        tools=tools,
        prompt=SYSTEM_PROMPT,
    )
