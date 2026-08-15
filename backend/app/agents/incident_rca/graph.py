"""事故根因分析 LangGraph：并行取证、假设验证循环与安全收敛。

架构位置：告警 Webhook/手动 API 把已校验的 ``alert`` 作为初始状态；本图并行查询日志、
指标和近期发布，汇聚后执行 ``analyze → verify``，达到确认阈值或迭代上限后生成报告。
worker 可保存最终 ``conclusion``，API 也可用 LangGraph 消息流转为 SSE。

典型调用示例::

    result = await build_rca_agent().ainvoke(
        {"alert": {"alertname": "HighLatency", "service": "checkout"}}
    )
    print(result["confidence"], result["conclusion"])

为什么采用这种图：三个取证系统互不依赖，扇出可降低总延迟；分析与验证分开并允许有限
循环，可显式暴露“提出了什么、验证到了什么”，比一次模型调用直接定责更安全。每个并行
节点只写自己的状态键，避免 LangGraph 并发合并冲突。

副作用与失败边界：会读取 Loki/日志源、Prometheus/监控、GitHub 并调用多次 LLM，但不会
自动回滚或变更生产系统。日志/指标客户端自行返回降级文本；发布查询在非本地环境失败时
绝不注入 mock。缺少任一证据会压低置信度并把最终结论标为待验证。当前 RCA 是辅助调查，
不是因果证明、自动定责或无人值守修复系统；人工复核和运行手册审批仍是生产边界。
"""

import math
from functools import lru_cache
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app.agents.incident_rca.analyzer import (
    ANALYZE_SYSTEM,
    CONCLUDE_SYSTEM,
    VERIFY_SYSTEM,
    format_alert,
    llm_json,
)
from app.core.config import get_settings
from app.core.logging import get_logger
from app.integrations.github import GitHubClient
from app.integrations.logs import LogClient
from app.integrations.monitoring import MonitoringClient
from app.kernel.llm.gateway import get_chat_model

logger = get_logger("agent.incident_rca")

_DEGRADED_MARKERS = ("未配置", "查询失败", "没有可用")


class RCAState(TypedDict, total=False):
    """一次 RCA 执行的增量状态。

    ``alert`` 是入口必填项；三个 fetch 节点分别写 ``logs``、``metrics``、``deploys``；
    ``analyze`` 写候选假设和迭代号，``verify`` 写验证结果/规范化置信度，``conclude`` 写最终
    Markdown。状态只在本次图执行中传播，不承担事故历史持久化。
    """

    alert: dict
    logs: str
    metrics: str
    deploys: str
    hypotheses: list[dict]
    verification: dict
    confidence: float
    iteration: int
    conclusion: str


# ---------- 并行拉取节点（无共享写入键，可安全并发）----------


async def _fetch_logs(state: RCAState) -> RCAState:
    """按告警 service 查询受限日志上下文，只写 ``logs`` 键。"""
    logs = await LogClient().query_logs(state["alert"].get("service", ""))
    return {"logs": logs}


async def _fetch_metrics(state: RCAState) -> RCAState:
    """按告警 service 查询监控摘要，只写 ``metrics`` 键。"""
    metrics = await MonitoringClient().query_metrics(state["alert"].get("service", ""))
    return {"metrics": metrics}


async def _fetch_deploys(state: RCAState) -> RCAState:
    """获取配置仓库的近期提交，并按证据字符预算裁剪。

    非本地环境缺配置或查询失败时返回明确降级标记；只有 local/test 才提供固定演示数据。
    这是关键安全边界：伪造发布记录会让模型产生看似可信但事实错误的事故结论。
    """
    settings = get_settings()
    repo = settings.deploy_repo
    if repo and "/" in repo:
        owner, name = repo.split("/", 1)
        try:
            commits = await GitHubClient().list_recent_commits(owner, name, limit=10)
            text = "\n".join(
                f"{c['sha']} {c['message']} ({c['author']} {c['date']})" for c in commits
            )
            return {"deploys": text[: settings.rca_max_evidence_chars] or "（无近期提交）"}
        except Exception as exc:  # noqa: BLE001 - 降级为 mock
            logger.warning("deploys_fetch_failed", error_type=type(exc).__name__)
            if not settings.is_local:
                return {"deploys": "（发布源查询失败，本次分析没有可用发布证据）"}
    elif not settings.is_local:
        logger.warning("deploys_unconfigured")
        return {"deploys": "（发布源未配置，无法获取真实发布记录）"}

    # mock 仅用于 local/test 演示，生产环境绝不把伪数据当作 RCA 证据。
    return {
        "deploys": (
            "a1b2c3d feat: 调整连接池上限从 200 降到 50 (2h ago)\n"
            "e4f5g6h chore: 升级依赖 (6h ago)\n"
            "i7j8k9l fix: 修复缓存击穿 (1d ago)"
        )
    }


# ---------- 分析 / 验证 / 收敛 ----------


def _context_block(state: RCAState) -> str:
    """把四类证据渲染成所有 RCA 提示共享的稳定结构。"""
    return (
        f"【告警】\n{format_alert(state['alert'])}\n\n"
        f"【错误日志】\n{state.get('logs', '')}\n\n"
        f"【监控指标】\n{state.get('metrics', '')}\n\n"
        f"【近期发布】\n{state.get('deploys', '')}"
    )


def _degraded_sources(state: RCAState) -> list[str]:
    """识别缺失/失败的数据源，供置信度上限与最终警告共同使用。

    检测基于各集成层返回的受控标记，而非让模型自行判断证据是否齐全，因而约束不会被
    模型输出绕过。若未来变更集成层降级文案，应同步更新标记或改为结构化状态。
    """
    labels = {"logs": "日志", "metrics": "指标", "deploys": "发布"}
    return [
        label
        for key, label in labels.items()
        if any(marker in state.get(key, "") for marker in _DEGRADED_MARKERS)
    ]


async def _analyze(state: RCAState) -> RCAState:
    """生成最多五个结构化根因假设，并推进迭代计数。

    上一轮验证理由会作为反馈输入，让模型修正而不是原样重复假设。解析失败降级为空列表，
    后续验证节点会以零置信度处理，避免把格式错误误判为成功分析。
    """
    iteration = state.get("iteration", 0) + 1
    feedback = ""
    if state.get("verification"):
        feedback = (
            f"\n\n【上一轮验证反馈】{state['verification'].get('reasoning', '')}"
            "（请据此修正或提出新的假设）"
        )
    result = await llm_json(ANALYZE_SYSTEM, _context_block(state) + feedback)
    hypotheses = (
        [item for item in result if isinstance(item, dict)][:5] if isinstance(result, list) else []
    )
    logger.info("rca_analyze", iteration=iteration, hypotheses=len(hypotheses))
    return {"hypotheses": hypotheses, "iteration": iteration}


async def _verify(state: RCAState) -> RCAState:
    """验证最高优先级假设，规范化并按证据完整性封顶置信度。

    模型给出的 ``confidence`` 可能是字符串、NaN、无穷或越界值，因此先转换并裁剪到
    ``[0, 1]``。每缺一个关键数据源就进一步降低上限；这是一条确定性业务规则，不依赖
    模型“自觉保守”。只有字面布尔值 ``confirmed is True`` 才能提前收敛。
    """
    hypotheses = state.get("hypotheses") or []
    if not hypotheses:
        return {
            "verification": {"confirmed": False, "reasoning": "无可验证假设"},
            "confidence": 0.0,
        }

    top = hypotheses[0]
    user = (
        f"{_context_block(state)}\n\n【待验证假设】\n{top.get('cause', '')}\n"
        f"【需验证点】{top.get('evidence_to_check', '')}"
    )
    result = await llm_json(VERIFY_SYSTEM, user)
    result = result if isinstance(result, dict) else {}
    try:
        confidence = float(result.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if not math.isfinite(confidence):
        confidence = 0.0
    confidence = max(0.0, min(confidence, 1.0))
    result["confidence"] = confidence
    degraded = _degraded_sources(state)
    if degraded:
        # 缺证据时不允许模型自行给出“高置信”结论。即便达到最大迭代次数，最终
        # 报告也会明确标为 degraded，而不是伪装成已验证根因。
        confidence_caps = {1: 0.7, 2: 0.5, 3: 0.2}
        confidence = min(confidence, confidence_caps[min(len(degraded), 3)])
        result["confidence"] = confidence
        result["degraded_sources"] = degraded
    logger.info("rca_verify", confirmed=result.get("confirmed"), confidence=confidence)
    return {"verification": result, "confidence": confidence}


def _route_after_verify(state: RCAState) -> str:
    """决定继续修正假设还是进入报告节点。

    达到确认阈值可提前结束；否则必须跑到配置的最大迭代数才结束。最大迭代数是成本与
    延迟保险丝，不代表到达上限后假设自动成立，最终警告仍会保留未确认状态。
    """
    settings = get_settings()
    verification = state.get("verification") or {}
    if (
        verification.get("confirmed") is True
        and state.get("confidence", 0.0) >= settings.rca_confidence_threshold
    ) or state.get("iteration", 0) >= settings.rca_max_iterations:
        return "conclude"
    return "analyze"


def _conclusion_warning(state: RCAState) -> str:
    """生成确定性的报告前缀，标记证据降级或未通过验证阈值。

    警告在模型输出之后由代码拼接，模型无法选择省略；这是防止幻觉报告被误当事实的最后
    一道呈现层保护。空字符串表示证据完整且假设确已越过阈值，并不代表无需人工复核。
    """
    warnings: list[str] = []
    degraded = _degraded_sources(state)
    if degraded:
        warnings.append(
            "证据降级：本次无法获取"
            + "、".join(degraded)
            + "数据；以下仅为待验证假设，不应直接作为事故定责依据。"
        )

    verification = state.get("verification") or {}
    confidence = state.get("confidence", 0.0)
    if (
        verification.get("confirmed") is not True
        or confidence < get_settings().rca_confidence_threshold
    ):
        warnings.append(
            f"假设尚未通过验证阈值（当前置信度 {confidence:.2f}）；请由值班人员补充证据并人工复核。"
        )
    if not warnings:
        return ""
    return "\n".join(f"> ⚠️ {warning}" for warning in warnings) + "\n\n"


async def _conclude(state: RCAState) -> RCAState:
    """请求模型生成最终 Markdown，并强制前置代码生成的安全警告。"""
    hypotheses = state.get("hypotheses") or []
    top = hypotheses[0] if hypotheses else {}
    user = (
        f"{_context_block(state)}\n\n"
        f"【当前最可能假设】{top.get('cause', '（无）')}\n"
        f"【验证结论】{state.get('verification', {})}\n\n"
        "请输出最终根因报告。"
    )
    model = get_chat_model()
    resp = await model.ainvoke([SystemMessage(CONCLUDE_SYSTEM), HumanMessage(user)])
    conclusion = str(resp.content)
    conclusion = _conclusion_warning(state) + conclusion
    return {"conclusion": conclusion}


@lru_cache(maxsize=1)
def build_rca_agent():
    """构建并缓存 RCA 图拓扑。

    LangGraph 会等待三个都指向 ``analyze`` 的上游分支完成后再执行汇聚节点。编译图可在
    进程内跨请求复用，实际 ``RCAState`` 仍按每次调用隔离。
    """
    graph = StateGraph(RCAState)
    graph.add_node("fetch_logs", _fetch_logs)
    graph.add_node("fetch_metrics", _fetch_metrics)
    graph.add_node("fetch_deploys", _fetch_deploys)
    graph.add_node("analyze", _analyze)
    graph.add_node("verify", _verify)
    graph.add_node("conclude", _conclude)

    # 并行扇出：三个数据源同时拉取
    graph.add_edge(START, "fetch_logs")
    graph.add_edge(START, "fetch_metrics")
    graph.add_edge(START, "fetch_deploys")
    # 汇聚：三者都完成后才进入 analyze
    graph.add_edge("fetch_logs", "analyze")
    graph.add_edge("fetch_metrics", "analyze")
    graph.add_edge("fetch_deploys", "analyze")
    # 假设验证循环
    graph.add_edge("analyze", "verify")
    graph.add_conditional_edges("verify", _route_after_verify, ["analyze", "conclude"])
    graph.add_edge("conclude", END)

    logger.info("rca_agent_build")
    return graph.compile()
