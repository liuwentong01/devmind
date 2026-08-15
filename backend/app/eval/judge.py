"""LLM-as-judge：依据参考答案为问答结果给出正确性/相关性分数。

调用链：``run_eval`` 取得 Agent 回答后调用 ``judge(question, reference, answer)``，本模块
用温度 0 的模型返回 ``{"score": 0..1, "reason": "..."}``。也可在异步测试脚本中直接
``verdict = await judge("问题", "参考", "回答")``。

生产边界：问题、参考和回答均是不可信文本，系统提示明确禁止服从其中指令；模型裁判具有
偏差且结果可能不可解析，因此分数被限制到有限的 0..1，失败保守记 0。发布门禁应结合
固定数据集、统计波动和人工抽检，不能以单次裁判替代事实验证。
"""

import math

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.incident_rca.analyzer import parse_json
from app.core.prompt_security import UNTRUSTED_CONTEXT_POLICY
from app.kernel.llm.gateway import get_chat_model

JUDGE_SYSTEM = (
    UNTRUSTED_CONTEXT_POLICY
    + "\n\n你是严格的评测员。对比【参考答案】判断【实际答案】是否正确、相关。\n"
    '只输出 JSON：{"score": 0.0-1.0, "reason": "简短理由"}。\n'
    "评分标准：1.0=完全正确且相关；0.5=部分正确或有缺失；0.0=错误或答非所问。"
)


async def judge(question: str, reference: str, answer: str) -> dict:
    """对单条问答打分，返回 ``{"score": float, "reason": str}``。

    任意模型格式偏差都会降级为 0 分，而不是让一次坏响应中断整个评测批次。
    """
    user = f"【问题】{question}\n\n【参考答案】{reference}\n\n【实际答案】{answer}"
    model = get_chat_model(temperature=0.0)
    resp = await model.ainvoke([SystemMessage(JUDGE_SYSTEM), HumanMessage(user)])
    parsed = parse_json(str(resp.content))
    if isinstance(parsed, dict) and "score" in parsed:
        try:
            score = float(parsed["score"])
            if not math.isfinite(score):
                raise ValueError
            return {
                "score": max(0.0, min(score, 1.0)),
                "reason": str(parsed.get("reason", ""))[:2000],
            }
        except (TypeError, ValueError):
            pass
    return {"score": 0.0, "reason": "评分解析失败"}
