"""知识答疑 Agent：自定义 LangGraph 工作流（retrieve → generate）。

与 P0 的预置 ReAct Agent 不同，这里展示如何手写一个状态机式工作流：
- retrieve 节点：向量检索，产出上下文切片与来源
- generate 节点：基于检索到的上下文，生成带 [n] 引用的回答

路由层通过 stream_mode=["updates", "messages"] 同时拿到「来源」与「流式 token」。
"""

from functools import lru_cache
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.kernel.llm.gateway import get_chat_model
from app.kernel.rag.retriever import RetrievedChunk, retrieve

logger = get_logger("agent.knowledge_qa")

SYSTEM_PROMPT = (
    "你是 DevMind 的知识答疑助手。严格依据【参考资料】回答用户问题。\n"
    "要求：\n"
    "1. 只使用参考资料中的信息，不要编造；资料不足以回答时，明确说『根据现有资料无法回答』。\n"
    "2. 在引用具体内容处用 [编号] 标注来源，编号对应参考资料的序号。\n"
    "3. 回答用简洁专业的中文。"
)


class QAState(TypedDict, total=False):
    """工作流状态。"""

    question: str
    session: AsyncSession
    top_k: int
    chunks: list[RetrievedChunk]
    sources: list[dict]
    answer: str


async def _retrieve_node(state: QAState) -> QAState:
    """检索节点：向量召回相关切片，整理来源列表。"""
    chunks = await retrieve(state["session"], state["question"], state.get("top_k"))
    sources = [
        {
            "index": i + 1,
            "document_id": c.document_id,
            "title": c.document_title,
            "score": c.score,
            "snippet": c.content[:120],
        }
        for i, c in enumerate(chunks)
    ]
    return {"chunks": chunks, "sources": sources}


async def _generate_node(state: QAState) -> QAState:
    """生成节点：用检索到的上下文构造 prompt 并调用 LLM（流式）。"""
    chunks = state.get("chunks", [])
    if chunks:
        context = "\n\n".join(
            f"[{i + 1}] 来源《{c.document_title}》：\n{c.content}" for i, c in enumerate(chunks)
        )
    else:
        context = "（无相关资料）"

    user_content = f"【参考资料】\n{context}\n\n【问题】\n{state['question']}"
    model = get_chat_model()
    # 流式 token 由 LangGraph 的 messages 模式在外层捕获
    resp = await model.ainvoke([SystemMessage(SYSTEM_PROMPT), HumanMessage(user_content)])
    return {"answer": resp.content}


@lru_cache(maxsize=1)
def build_knowledge_agent():
    """构建并编译知识答疑工作流（进程内复用）。"""
    graph = StateGraph(QAState)
    graph.add_node("retrieve", _retrieve_node)
    graph.add_node("generate", _generate_node)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    logger.info("knowledge_agent_build")
    return graph.compile()
