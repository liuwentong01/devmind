"""代码理解 Agent：问答工作流（locate → answer）。

locate：从问题里提取标识符，在 Neo4j 图谱检索相关函数作为上下文；
answer：基于上下文流式回答。
"""

from functools import lru_cache
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app.agents.code_understanding.agent import QA_SYSTEM, build_context, format_context
from app.core.logging import get_logger
from app.kernel.llm.gateway import get_chat_model

logger = get_logger("agent.code_understanding.graph")


class CodeQAState(TypedDict, total=False):
    repo: str
    question: str
    context: list[dict]
    answer: str


async def _locate_node(state: CodeQAState) -> CodeQAState:
    context = await build_context(state["repo"], state["question"])
    return {"context": context}


async def _answer_node(state: CodeQAState) -> CodeQAState:
    user = (
        f"【相关函数代码】\n{format_context(state.get('context', []))}\n\n"
        f"【问题】\n{state['question']}"
    )
    model = get_chat_model()
    resp = await model.ainvoke([SystemMessage(QA_SYSTEM), HumanMessage(user)])
    return {"answer": str(resp.content)}


@lru_cache(maxsize=1)
def build_code_qa_agent():
    graph = StateGraph(CodeQAState)
    graph.add_node("locate", _locate_node)
    graph.add_node("answer", _answer_node)
    graph.add_edge(START, "locate")
    graph.add_edge("locate", "answer")
    graph.add_edge("answer", END)
    logger.info("code_qa_agent_build")
    return graph.compile()
