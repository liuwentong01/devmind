"""LLM-as-judge：用 LLM 给答案的正确性 / 相关性打分。"""

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.incident_rca.analyzer import parse_json
from app.kernel.llm.gateway import get_chat_model

JUDGE_SYSTEM = (
    "你是严格的评测员。对比【参考答案】判断【实际答案】是否正确、相关。\n"
    "只输出 JSON：{\"score\": 0.0-1.0, \"reason\": \"简短理由\"}。\n"
    "评分标准：1.0=完全正确且相关；0.5=部分正确或有缺失；0.0=错误或答非所问。"
)


async def judge(question: str, reference: str, answer: str) -> dict:
    """对单条问答打分，返回 {score, reason}。"""
    user = (
        f"【问题】{question}\n\n【参考答案】{reference}\n\n【实际答案】{answer}"
    )
    model = get_chat_model(temperature=0.0)
    resp = await model.ainvoke([SystemMessage(JUDGE_SYSTEM), HumanMessage(user)])
    parsed = parse_json(str(resp.content))
    if isinstance(parsed, dict) and "score" in parsed:
        try:
            return {"score": float(parsed["score"]), "reason": parsed.get("reason", "")}
        except (TypeError, ValueError):
            pass
    return {"score": 0.0, "reason": "评分解析失败"}
