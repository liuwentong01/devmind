"""代码审查的无传输层核心：检索规范、裁剪 diff、构造模型消息。

架构位置：交互式 LangGraph 与 GitHub Webhook 的 ARQ worker 都调用本模块，从而保证两条
入口使用同一套安全提示词与长度边界。输入是外部不可信 diff 和数据库会话；输出是规范
文本列表或 LangChain 消息，不直接调用审查模型。

典型调用示例::

    rules = await load_review_rules(session, patch_text)
    messages = build_review_messages(patch_text, rules)
    response = await get_chat_model().ainvoke(messages)

副作用与失败边界：规范检索会访问 Embedding 服务和 PostgreSQL，但不会提交事务；消息
构造是纯函数。diff 最多把 ``MAX_DIFF_CHARS`` 个字符交给模型，超出部分会明确标记截断。
团队规范也被当作不可信参考资料，系统提示中的注入防护要求模型忽略其中的指令。当前能力
只评论展示的变更，不具备完整代码上下文、测试执行或 GitHub 回写幂等性。
"""

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.prompt_security import UNTRUSTED_CONTEXT_POLICY
from app.kernel.rag.retriever import retrieve

# 单次审查送入 LLM 的 diff 上限，避免超长上下文（超出截断）
MAX_DIFF_CHARS = 12000

SYSTEM_PROMPT = (
    UNTRUSTED_CONTEXT_POLICY
    + "\n\n你是 DevMind 的资深代码审查助手，审查 GitHub Pull Request 的 diff。\n"
    "审查维度：正确性与潜在 bug、边界与异常处理、安全风险、性能、可读性与命名、"
    "是否符合给出的【团队规范】。\n"
    "输出要求（Markdown）：\n"
    "1. 先给一句总体结论（建议合并 / 建议修改后合并 / 不建议合并）。\n"
    "2. 用列表列出问题，每条标注严重级别（🔴阻塞 / 🟡建议 / 🟢优化）、涉及文件，并给出修改建议。\n"
    "3. 只针对 diff 中实际改动的内容评论，不臆测未展示的代码。\n"
    "4. 若改动没有明显问题，明确说明并指出值得肯定的地方。"
)


async def load_review_rules(session: AsyncSession, diff: str) -> list[str]:
    """从知识库检索与本次改动相关的团队审查规范片段。

    复用知识库向量检索：团队规范以 ``source=review-rule``（实际值由配置决定）的文档
    形式入库。只取 diff 前部作为 query，是因为这里通常含文件路径与变更头，且避免为了
    检索再次发送整个大 diff。

    Args:
        session: 请求级异步数据库会话，事务所有权仍属于调用方。
        diff: Git diff 文本；即使来自 GitHub，也必须按不可信内容处理。

    Returns:
        按相似度排序的规范切片正文，最多 ``review_top_k`` 条。

    Raises:
        ValueError: Embedding 返回不合法或检索参数越界。
        sqlalchemy.exc.SQLAlchemyError: 数据库查询失败。
    """
    settings = get_settings()
    # diff 头部通常包含路径、hunk 位置和首批关键改动；用固定上限还能控制 embedding 成本。
    query = diff[:1000]
    chunks = await retrieve(
        session,
        query,
        top_k=settings.review_top_k,
        source=settings.review_rule_source,
    )
    return [c.content for c in chunks]


def build_review_messages(diff: str, rules: list[str]) -> list:
    """根据 diff 与规范片段构造系统/用户消息。

    Args:
        diff: 待审查的统一 diff。
        rules: 与变更相关的团队规范正文；允许为空。

    Returns:
        ``[SystemMessage, HumanMessage]``，可直接传给 LangChain ChatModel。

    示例::

        messages = build_review_messages("+ timeout = 30", ["网络调用必须设超时"])

    截断发生在字符边界而非语义边界，所以提示中显式告知模型“diff 过长已截断”；模型不得
    对未展示部分作推断。规范和 diff 都放在 HumanMessage 的数据区，安全策略仍由更高优先级
    的 SystemMessage 持有。
    """
    truncated = diff[:MAX_DIFF_CHARS]
    if len(diff) > MAX_DIFF_CHARS:
        truncated += "\n\n...(diff 过长已截断)..."

    rules_text = "\n".join(f"- {r}" for r in rules) if rules else "（知识库中暂无相关团队规范）"
    user_content = f"【团队规范】\n{rules_text}\n\n【待审查的 PR diff】\n```diff\n{truncated}\n```"
    return [SystemMessage(SYSTEM_PROMPT), HumanMessage(user_content)]
