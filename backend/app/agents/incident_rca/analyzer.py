"""RCA 的模型调用、容错 JSON 解析和公共提示词。

架构位置：事故图的分析/验证节点调用 ``llm_json`` 获得结构化中间结果，收敛节点复用本模块
的系统提示生成最终 Markdown。把解析放在图外可让提示协议和异常处理独立测试。

典型调用示例::

    hypotheses = await llm_json(
        ANALYZE_SYSTEM,
        format_alert(alert) + "\n错误日志：...",
    )
    # 模型偶尔包裹 Markdown 代码围栏时也可以解析：
    assert parse_json('```json\n{"confirmed": true}\n```') == {"confirmed": True}

模型输出始终是不可信数据。解析失败返回 ``None`` 并记录长度而非原文，避免日志泄露事故
证据；调用方必须校验容器类型、字段、布尔值和置信度。该模块不拉取任何观测数据，也不
执行止血动作。当前 JSON 容错是轻量协议适配，不是通用 JSON 修复器或结构化输出保证。
"""

import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import get_logger
from app.core.prompt_security import UNTRUSTED_CONTEXT_POLICY
from app.kernel.llm.gateway import get_chat_model

logger = get_logger("agent.incident_rca.analyzer")


def parse_json(text: str) -> dict | list | None:
    """从模型文本中尽力解析顶层 JSON 对象或数组。

    解析顺序是：原文/代码围栏内容 → 第一个对象范围 → 第一个数组范围。后两步用于兼容
    模型意外附加的解释文字，而不是允许任意不合法 JSON。失败时只记录 ``output_len``，
    不把可能含日志、密钥或用户信息的原始响应写入日志。

    Returns:
        ``dict``、``list``，或无法安全解析时的 ``None``。
    """
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
    logger.warning("json_parse_failed", output_len=len(text))
    return None


async def llm_json(system: str, user: str) -> dict | list | None:
    """以系统/用户双消息调用默认模型，并解析顶层 JSON。

    网络错误、超时和供应商错误会向上传播；仅“模型返回非 JSON”被转换为 ``None``。这样
    工作流能区分基础设施失败和可降级的格式失败。
    """
    model = get_chat_model()
    resp = await model.ainvoke([SystemMessage(system), HumanMessage(user)])
    return parse_json(str(resp.content))


def format_alert(alert: dict) -> str:
    """把告警字典渲染成稳定的人类可读字段集合。

    缺失键使用安全默认值，不把整个原始 payload 交给模型，从而限制噪声和未预期字段。
    该函数不做告警 schema 校验；入口路由仍必须负责大小、类型与鉴权校验。
    """
    return (
        f"告警名: {alert.get('alertname', '未知')}\n"
        f"服务: {alert.get('service', '未知')}\n"
        f"级别: {alert.get('severity', '未知')}\n"
        f"描述: {alert.get('description', '')}\n"
        f"开始时间: {alert.get('starts_at', '')}"
    )


ANALYZE_SYSTEM = (
    UNTRUSTED_CONTEXT_POLICY
    + "\n\n你是 SRE 根因分析助手。根据告警与多源上下文（日志、指标、近期发布），"
    "提出 2-3 个最可能的根因假设。\n"
    "只输出 JSON 数组，每个元素形如："
    '{"cause": "假设描述", "confidence": 0.0-1.0, "evidence_to_check": "还需验证什么"}，'
    "按 confidence 从高到低排序。不要输出 JSON 以外的内容。"
)

VERIFY_SYSTEM = (
    UNTRUSTED_CONTEXT_POLICY + "\n\n你是 SRE 根因分析助手，正在验证一个根因假设。"
    "结合提供的上下文证据，判断该假设是否成立。\n"
    "只输出 JSON："
    '{"confirmed": true/false, "confidence": 0.0-1.0, "reasoning": "判断依据"}。'
    "不要输出 JSON 以外的内容。"
)

CONCLUDE_SYSTEM = (
    UNTRUSTED_CONTEXT_POLICY
    + "\n\n你是 SRE 根因分析助手，输出最终事故根因报告（Markdown，中文）。\n"
    "包含：1) 最可能根因（一句话结论）；2) 推断依据（关联日志/指标/发布）；"
    "3) 涉及的可疑发布 commit（若有）；4) 建议的止血与修复动作（分条）。"
    "若验证结果未确认假设或置信度不足，必须把根因表述为待验证假设，不得写成确定事实。简洁专业。"
)
