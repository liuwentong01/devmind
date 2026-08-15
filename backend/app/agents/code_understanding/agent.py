"""代码理解的检索与影响分析辅助函数。

架构位置：本模块连接业务智能体与 Neo4j 代码图谱。问答图先调用 ``build_context``，再由
``format_context`` 生成受限上下文；影响分析入口 ``analyze_impact`` 则查询调用者/被调用者
并让 LLM 总结回归范围。

典型调用示例::

    functions = await build_context("devmind", "get_queue 在哪里初始化？")
    prompt_context = format_context(functions)
    report = await analyze_impact("devmind", "get_queue", depth=2)

输入问题、仓库名和图谱代码均按不可信数据处理。调用会读取 Neo4j，影响总结还会访问 LLM；
不会写图谱。为防止上下文/费用失控，函数命中数和拼接字符数都有上限。当前检索主要依赖
问题中的英文标识符，调用图又是按函数名朴素连接，因此中文概念、同名函数、动态派发可能
漏召回或误关联；结果是工程导航线索，不是编译器级依赖证明。
"""

import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import get_logger
from app.core.prompt_security import UNTRUSTED_CONTEXT_POLICY
from app.kernel.graph import store
from app.kernel.llm.gateway import get_chat_model

logger = get_logger("agent.code_understanding")

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
MAX_CODE_CONTEXT_CHARS = 24_000

QA_SYSTEM = (
    UNTRUSTED_CONTEXT_POLICY
    + "\n\n你是代码库理解助手。依据给出的【相关函数代码】回答用户对代码库的问题，"
    "解释职责、调用关系、实现要点。资料不足时明说，不要臆造。用简洁中文回答。"
)

IMPACT_SYSTEM = (
    UNTRUSTED_CONTEXT_POLICY + "\n\n你是代码影响分析助手。根据给出的目标函数、其调用者和被调用者，"
    "评估『修改该函数可能影响的范围』和需要回归测试的点。用简洁中文，分条输出。"
)


async def build_context(repo: str, question: str, limit: int = 6) -> list[dict]:
    """从问题提取候选标识符，按顺序在同仓库图谱中检索相关函数。

    Args:
        repo: 索引时使用的仓库逻辑名，承担图谱隔离作用。
        question: 用户问题；正则只提取长度至少为 3 的英文/下划线标识符。
        limit: 最终返回函数数量上限；每个 token 的底层查询也使用该上限。

    Returns:
        Neo4j 函数字典列表，按首次命中顺序去重，键通常包含 ``path``、``name``、
        ``code`` 与行号。

    ``dict.fromkeys`` 用于去重并保留问题顺序，因为靠前的标识符通常更接近用户主语；函数
    再按 ``path::name`` 去重，避免多个 token 命中同一段代码造成重复 token 消耗。
    """
    tokens = list(dict.fromkeys(_TOKEN_RE.findall(question)))  # 去重保序
    seen: dict[str, dict] = {}
    for tok in tokens:
        for fn in await store.search_functions(repo, tok, limit=limit):
            seen.setdefault(f"{fn['path']}::{fn['name']}", fn)
        if len(seen) >= limit:
            break
    return list(seen.values())[:limit]


def format_context(functions: list[dict]) -> str:
    """把图谱函数渲染成模型可读的代码块，并施加全局字符预算。

    空列表会返回明确占位符，让模型知道是“无证据”而非提示构造遗漏。预算按顺序消耗；
    最后一个块可能截断并带标记。该函数是纯函数，不验证代码真实性，调用方必须配合系统
    提示防止仓库注释中的 prompt injection。
    """
    if not functions:
        return "（图谱中未检索到相关函数）"
    blocks = []
    remaining = MAX_CODE_CONTEXT_CHARS
    for fn in functions:
        block = f"// {fn['path']}:{fn.get('start_line')} 函数 {fn['name']}\n{fn.get('code', '')}"
        if len(block) > remaining:
            block = block[:remaining] + "\n...(代码上下文已截断)..."
        blocks.append(block)
        remaining -= min(len(block), remaining)
        if remaining <= 0:
            break
    return "\n\n".join(blocks)


async def analyze_impact(repo: str, name: str, depth: int = 3) -> dict:
    """查询目标函数两侧调用关系，并生成面向回归测试的影响摘要。

    Args:
        repo: 仓库逻辑名。
        name: 目标函数名；同仓库同名节点当前会共同参与查询。
        depth: 反向查找调用者的最大 CALLS 跳数，底层查询限制最终最多 100 条。

    Returns:
        ``{"callers": ..., "callees": ..., "summary": ...}``。原始图结果保留给 UI/调用方
        核验，``summary`` 只是模型解释。

    Raises:
        neo4j.exceptions.Neo4jError: 图数据库不可用或查询失败。
        Exception: LLM 调用失败；本层不静默伪造分析。

    发送给模型的每侧列表再限制到 50 条并受总字符预算约束，避免高扇出函数放大提示词。
    """
    callers = await store.find_callers(repo, name, depth=depth)
    callees = await store.find_callees(repo, name)

    user = (
        f"目标函数：{name}\n\n"
        f"调用它的函数（共 {len(callers)} 个）：\n"
        + "\n".join(f"- {c['name']} @ {c['path']}" for c in callers[:50])
        + f"\n\n它调用的函数（共 {len(callees)} 个）：\n"
        + "\n".join(f"- {c['name']} @ {c['path']}" for c in callees[:50])
    )
    user = user[:MAX_CODE_CONTEXT_CHARS]
    model = get_chat_model()
    resp = await model.ainvoke([SystemMessage(IMPACT_SYSTEM), HumanMessage(user)])
    return {"callers": callers, "callees": callees, "summary": str(resp.content)}
