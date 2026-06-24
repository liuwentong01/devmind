"""代码审查 Agent：LangGraph 工作流（load_rules → review）。

用于交互式 SSE 审查（前端粘 diff）。Webhook 异步路径见 app/workers/review_worker.py，
两者共用 reviewer.py 的 prompt 构造与规范检索。
"""

from functools import lru_cache
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.code_review.reviewer import build_review_messages, load_review_rules
from app.core.logging import get_logger
from app.kernel.llm.gateway import get_chat_model

logger = get_logger("agent.code_review")


class ReviewState(TypedDict, total=False):
    """审查工作流状态。"""

    diff: str
    session: AsyncSession
    rules: list[str]
    review: str


async def _load_rules_node(state: ReviewState) -> ReviewState:
    """检索团队审查规范。"""
    rules = await load_review_rules(state["session"], state["diff"])
    return {"rules": rules}


async def _review_node(state: ReviewState) -> ReviewState:
    """基于 diff + 规范生成审查意见（流式 token 由外层 messages 模式捕获）。"""
    messages = build_review_messages(state["diff"], state.get("rules", []))
    model = get_chat_model()
    resp = await model.ainvoke(messages)
    return {"review": resp.content}


@lru_cache(maxsize=1)
def build_review_agent():
    """构建并编译代码审查工作流。"""
    graph = StateGraph(ReviewState)
    graph.add_node("load_rules", _load_rules_node)
    graph.add_node("review", _review_node)
    graph.add_edge(START, "load_rules")
    graph.add_edge("load_rules", "review")
    graph.add_edge("review", END)
    logger.info("review_agent_build")
    return graph.compile()
