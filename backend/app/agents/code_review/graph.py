"""代码审查 Agent 的 LangGraph 工作流：``load_rules → review``。

架构位置：交互式审查路由把用户粘贴的 diff 与请求级数据库会话作为初始状态传进本图；
Webhook 异步路径由 ``app.workers.review_worker`` 编排，但复用 :mod:`.reviewer` 的检索和
提示词逻辑。图的输出 ``review`` 是 Markdown，外层可用 LangGraph ``messages`` 模式捕获
模型 token 并转成 SSE。

典型调用示例::

    graph = build_review_agent()
    result = await graph.ainvoke({"diff": patch_text, "session": db_session})
    print(result["rules"], result["review"])

状态与副作用：``load_rules`` 读取 PostgreSQL/pgvector 并调用 Embedding 服务，``review``
调用 LLM；本模块不提交数据库事务，也不回写 GitHub。任一依赖失败会向上传播，由路由或
队列重试策略处理。当前图只审查可见 diff 与召回规范，不能替代完整仓库分析、编译测试、
SAST 或人工批准；这些能力是外部流水线或后续路线。
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
    """节点之间传递的请求级状态，而不是跨请求共享存储。

    ``diff`` 与 ``session`` 是入口必填项；``rules`` 由检索节点写入，``review`` 由模型节点
    写入。使用 ``total=False`` 是为了让每个节点只返回自己新增的键，LangGraph 会把结果
    合并回状态；它不表示入口真的可以省略必填字段。
    """

    diff: str
    session: AsyncSession
    rules: list[str]
    review: str


async def _load_rules_node(state: ReviewState) -> ReviewState:
    """按 diff 的文件路径/关键改动检索团队规范，只写入 ``rules`` 状态键。

    为什么先检索再生成：把团队约定作为显式证据传给模型，既能复用企业知识库，也能让
    调用者看到检索结果；同时避免在审查 prompt 中硬编码易过期的规则。
    """
    rules = await load_review_rules(state["session"], state["diff"])
    return {"rules": rules}


async def _review_node(state: ReviewState) -> ReviewState:
    """基于 diff 与规则生成意见，并把完整结果写入 ``review``。

    这里使用 ``ainvoke`` 仍可被 LangGraph 的 ``messages`` 流模式观察到模型消息块；节点
    自身不直接管理 SSE，避免把传输协议耦合进业务图。
    """
    messages = build_review_messages(state["diff"], state.get("rules", []))
    model = get_chat_model()
    resp = await model.ainvoke(messages)
    return {"review": resp.content}


@lru_cache(maxsize=1)
def build_review_agent():
    """构建并编译代码审查图，同一进程内只创建一次。

    Returns:
        可调用/可流式执行的编译图。图对象可跨请求复用，但传入的 ``ReviewState`` 和
        ``AsyncSession`` 必须保持请求隔离。

    ``lru_cache`` 只缓存无业务状态的拓扑，减少每次请求重复编译；不要把请求数据闭包进
    节点，否则会破坏多用户隔离。
    """
    graph = StateGraph(ReviewState)
    graph.add_node("load_rules", _load_rules_node)
    graph.add_node("review", _review_node)
    graph.add_edge(START, "load_rules")
    graph.add_edge("load_rules", "review")
    graph.add_edge("review", END)
    logger.info("review_agent_build")
    return graph.compile()
