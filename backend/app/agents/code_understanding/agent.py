"""代码理解辅助：基于图谱构造上下文、做影响分析。"""

import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import get_logger
from app.kernel.graph import store
from app.kernel.llm.gateway import get_chat_model

logger = get_logger("agent.code_understanding")

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

QA_SYSTEM = (
    "你是代码库理解助手。依据给出的【相关函数代码】回答用户对代码库的问题，"
    "解释职责、调用关系、实现要点。资料不足时明说，不要臆造。用简洁中文回答。"
)

IMPACT_SYSTEM = (
    "你是代码影响分析助手。根据给出的目标函数、其调用者和被调用者，"
    "评估『修改该函数可能影响的范围』和需要回归测试的点。用简洁中文，分条输出。"
)


async def build_context(repo: str, question: str, limit: int = 6) -> list[dict]:
    """从问题中提取标识符 token，在图谱里检索相关函数作为上下文。"""
    tokens = list(dict.fromkeys(_TOKEN_RE.findall(question)))  # 去重保序
    seen: dict[str, dict] = {}
    for tok in tokens:
        for fn in await store.search_functions(repo, tok, limit=limit):
            seen.setdefault(f"{fn['path']}::{fn['name']}", fn)
        if len(seen) >= limit:
            break
    return list(seen.values())[:limit]


def format_context(functions: list[dict]) -> str:
    if not functions:
        return "（图谱中未检索到相关函数）"
    blocks = []
    for fn in functions:
        blocks.append(
            f"// {fn['path']}:{fn.get('start_line')} 函数 {fn['name']}\n{fn.get('code', '')}"
        )
    return "\n\n".join(blocks)


async def analyze_impact(repo: str, name: str, depth: int = 3) -> dict:
    """影响分析：找调用者（影响面）+ 被调用者，并让 LLM 总结。"""
    callers = await store.find_callers(repo, name, depth=depth)
    callees = await store.find_callees(repo, name)

    user = (
        f"目标函数：{name}\n\n"
        f"调用它的函数（共 {len(callers)} 个）：\n"
        + "\n".join(f"- {c['name']} @ {c['path']}" for c in callers[:50])
        + f"\n\n它调用的函数（共 {len(callees)} 个）：\n"
        + "\n".join(f"- {c['name']} @ {c['path']}" for c in callees[:50])
    )
    model = get_chat_model()
    resp = await model.ainvoke([SystemMessage(IMPACT_SYSTEM), HumanMessage(user)])
    return {"callers": callers, "callees": callees, "summary": str(resp.content)}
