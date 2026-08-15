"""代码理解问答 LangGraph：``locate → answer``。

架构位置：代码索引 API/worker 先通过 :mod:`app.kernel.graph` 建立 Neo4j 图；问答路由随后
把 ``repo`` 和 ``question`` 交给本图。``locate`` 查找相关函数，``answer`` 把受限代码上下文
交给 LLM，最终状态同时保留命中函数和回答，便于 UI 展示证据。

典型调用示例::

    graph = build_code_qa_agent()
    result = await graph.ainvoke(
        {"repo": "devmind", "question": "replace_repo 为什么是原子的？"}
    )

图会读取 Neo4j 和调用 LLM，不会触发索引或写数据库；索引不存在时会明确提供“未检索到”
上下文。外层可监听 ``messages`` 获取流式 token。当前图基于符号名定位，不能保证完整语义
理解，也没有跨仓库/分支版本选择；这些是未来索引演进方向。
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
    """一次代码问答的短生命周期状态。

    入口需要 ``repo``、``question``；定位节点填充 ``context``，回答节点填充 ``answer``。
    ``total=False`` 支持节点返回增量键，但不意味着入口字段在运行时可缺失。
    """

    repo: str
    question: str
    context: list[dict]
    answer: str


async def _locate_node(state: CodeQAState) -> CodeQAState:
    """从 Neo4j 定位候选函数，仅更新 ``context``。"""
    context = await build_context(state["repo"], state["question"])
    return {"context": context}


async def _answer_node(state: CodeQAState) -> CodeQAState:
    """基于候选代码回答问题，仅更新 ``answer``。

    仓库源代码可能包含恶意注释，因此它和用户问题一并放在 HumanMessage 中，并由
    ``QA_SYSTEM`` 的不可信上下文策略约束。完整 token 流由外层 LangGraph 流模式处理。
    """
    user = (
        f"【相关函数代码】\n{format_context(state.get('context', []))}\n\n"
        f"【问题】\n{state['question']}"
    )
    model = get_chat_model()
    resp = await model.ainvoke([SystemMessage(QA_SYSTEM), HumanMessage(user)])
    return {"answer": str(resp.content)}


@lru_cache(maxsize=1)
def build_code_qa_agent():
    """编译并缓存无请求状态的问答图。

    Returns:
        支持 ``ainvoke``/``astream`` 的编译图。同一对象可跨请求复用，状态在每次调用中
        单独传递。
    """
    graph = StateGraph(CodeQAState)
    graph.add_node("locate", _locate_node)
    graph.add_node("answer", _answer_node)
    graph.add_edge(START, "locate")
    graph.add_edge("locate", "answer")
    graph.add_edge("answer", END)
    logger.info("code_qa_agent_build")
    return graph.compile()
