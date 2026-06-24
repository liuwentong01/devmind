"""根因分析辅助：LLM 调用封装 + JSON 解析 + prompt 构造。"""

import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import get_logger
from app.kernel.llm.gateway import get_chat_model

logger = get_logger("agent.incident_rca.analyzer")


def parse_json(text: str) -> dict | list | None:
    """从 LLM 输出中尽力解析 JSON（容忍 ```json 代码块包裹）。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1] if "```" in cleaned[3:] else cleaned[3:]
        cleaned = cleaned.removeprefix("json").strip()
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        # 尝试截取第一个 { 或 [ 到最后一个 } 或 ]
        for start, end in (("{", "}"), ("[", "]")):
            i, j = cleaned.find(start), cleaned.rfind(end)
            if i != -1 and j != -1 and j > i:
                try:
                    return json.loads(cleaned[i : j + 1])
                except (json.JSONDecodeError, ValueError):
                    continue
    logger.warning("json_parse_failed", preview=text[:120])
    return None


async def llm_json(system: str, user: str) -> dict | list | None:
    """调用 LLM 并解析为 JSON。"""
    model = get_chat_model()
    resp = await model.ainvoke([SystemMessage(system), HumanMessage(user)])
    return parse_json(str(resp.content))


def format_alert(alert: dict) -> str:
    """把告警结构化信息转成文本。"""
    return (
        f"告警名: {alert.get('alertname', '未知')}\n"
        f"服务: {alert.get('service', '未知')}\n"
        f"级别: {alert.get('severity', '未知')}\n"
        f"描述: {alert.get('description', '')}\n"
        f"开始时间: {alert.get('starts_at', '')}"
    )


ANALYZE_SYSTEM = (
    "你是 SRE 根因分析助手。根据告警与多源上下文（日志、指标、近期发布），"
    "提出 2-3 个最可能的根因假设。\n"
    "只输出 JSON 数组，每个元素形如："
    '{"cause": "假设描述", "confidence": 0.0-1.0, "evidence_to_check": "还需验证什么"}，'
    "按 confidence 从高到低排序。不要输出 JSON 以外的内容。"
)

VERIFY_SYSTEM = (
    "你是 SRE 根因分析助手，正在验证一个根因假设。"
    "结合提供的上下文证据，判断该假设是否成立。\n"
    "只输出 JSON："
    '{"confirmed": true/false, "confidence": 0.0-1.0, "reasoning": "判断依据"}。'
    "不要输出 JSON 以外的内容。"
)

CONCLUDE_SYSTEM = (
    "你是 SRE 根因分析助手，输出最终事故根因报告（Markdown，中文）。\n"
    "包含：1) 最可能根因（一句话结论）；2) 推断依据（关联日志/指标/发布）；"
    "3) 涉及的可疑发布 commit（若有）；4) 建议的止血与修复动作（分条）。简洁专业。"
)
