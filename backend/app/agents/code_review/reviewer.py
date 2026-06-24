"""代码审查核心：构造 prompt、裁剪 diff、检索团队规范。

被 LangGraph 工作流（交互式 SSE）和 ARQ worker（Webhook 回写）共用。
"""

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.kernel.rag.retriever import retrieve

# 单次审查送入 LLM 的 diff 上限，避免超长上下文（超出截断）
MAX_DIFF_CHARS = 12000

SYSTEM_PROMPT = (
    "你是 DevMind 的资深代码审查助手，审查 GitHub Pull Request 的 diff。\n"
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

    复用 P1 的向量检索：团队规范以知识文档形式入库（source=review-rule）。
    """
    settings = get_settings()
    # 用 diff 的前若干字符作为检索 query（包含文件路径与关键改动）
    query = diff[:1000]
    chunks = await retrieve(session, query, top_k=settings.review_top_k)
    return [c.content for c in chunks]


def build_review_messages(diff: str, rules: list[str]) -> list:
    """根据 diff 与规范片段拼装 LLM 消息。"""
    truncated = diff[:MAX_DIFF_CHARS]
    if len(diff) > MAX_DIFF_CHARS:
        truncated += "\n\n...(diff 过长已截断)..."

    rules_text = "\n".join(f"- {r}" for r in rules) if rules else "（知识库中暂无相关团队规范）"
    user_content = (
        f"【团队规范】\n{rules_text}\n\n【待审查的 PR diff】\n```diff\n{truncated}\n```"
    )
    return [SystemMessage(SYSTEM_PROMPT), HumanMessage(user_content)]
