"""企业知识答疑 LangGraph：``retrieve → generate``。

架构位置：文档先经 :mod:`app.kernel.rag.retriever` 入库；问答路由再把问题、请求级数据库
会话和可选 ``top_k`` 传入本图。检索节点产生完整切片与可展示的来源摘要，生成节点只依据
这些切片回答并标注 ``[n]`` 引用。路由可用 ``stream_mode=["updates", "messages"]`` 同时
获得来源更新和模型 token。

典型调用示例::

    graph = build_knowledge_agent()
    result = await graph.ainvoke(
        {"question": "值班升级流程是什么？", "session": db_session, "top_k": 5}
    )
    print(result["sources"], result["answer"])

为什么不用通用 ReAct：RAG 的步骤和数据来源是固定的，显式状态机更容易审计引用、限制
数据库访问并测试每个节点。检索会调用 Embedding 服务并读取 PostgreSQL，生成会调用 LLM；
图不提交事务。资料为空时仍进入生成节点，但提示明确“无相关资料”，系统要求模型拒绝
臆造。当前引用表示使用了某切片，不证明材料正确、时效或用户有权访问；文档 ACL、混合
检索、重排与事实验证仍是生产治理/未来能力。
"""

from functools import lru_cache
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.prompt_security import UNTRUSTED_CONTEXT_POLICY
from app.kernel.llm.gateway import get_chat_model
from app.kernel.rag.retriever import RetrievedChunk, retrieve

logger = get_logger("agent.knowledge_qa")

SYSTEM_PROMPT = (
    UNTRUSTED_CONTEXT_POLICY
    + "\n\n你是 DevMind 的知识答疑助手。严格依据【参考资料】回答用户问题。\n"
    "要求：\n"
    "1. 只使用参考资料中的信息，不要编造；资料不足以回答时，明确说『根据现有资料无法回答』。\n"
    "2. 在引用具体内容处用 [编号] 标注来源，编号对应参考资料的序号。\n"
    "3. 回答用简洁专业的中文。"
)


class QAState(TypedDict, total=False):
    """一次知识问答的请求级增量状态。

    入口必填 ``question``、``session``，``top_k`` 可选；检索节点添加 ``chunks`` 和
    ``sources``，生成节点添加 ``answer``。``chunks`` 含完整正文，只应在服务端状态中流转；
    对客户端通常只发送经过裁剪的 ``sources``。
    """

    question: str
    session: AsyncSession
    top_k: int
    chunks: list[RetrievedChunk]
    sources: list[dict]
    answer: str


async def _retrieve_node(state: QAState) -> QAState:
    """向量召回相关切片，并构造对外可展示的来源摘要。

    ``snippet`` 只保留前 120 个字符，既让用户能核验引用，又避免 ``updates`` 流意外发送
    整篇内部文档。完整正文仅写入 ``chunks`` 供下一节点使用。
    """
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
    """把编号切片和问题构造成安全提示并生成回答。

    文档正文可能含 prompt injection，因此作为 HumanMessage 的参考数据传入，并由更高
    优先级 ``SYSTEM_PROMPT`` 明确限制。此处 ``ainvoke`` 产生的消息事件仍可由 LangGraph
    ``messages`` 流模式捕获；本节点不依赖 SSE 传输层。
    """
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
    """编译并缓存无请求状态的知识问答图。

    Returns:
        支持 ``ainvoke``/``astream`` 的编译图。图可跨请求复用，传入的数据库会话和状态
        不可跨请求共享。
    """
    graph = StateGraph(QAState)
    graph.add_node("retrieve", _retrieve_node)
    graph.add_node("generate", _generate_node)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    logger.info("knowledge_agent_build")
    return graph.compile()
