"""告警根因 Agent：LangGraph 工作流。

体现两个核心能力：
1. 多系统并行编排：START 同时扇出到 fetch_logs / fetch_metrics / fetch_deploys，
   三者并发执行，全部完成后汇聚到 analyze（join）。
2. 假设验证循环：analyze 生成假设 → verify 验证 → 未达置信度且未超迭代上限则回到
   analyze 重新分析，否则进入 conclude。
"""

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


class RCAState(TypedDict, total=False):
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
    logs = await LogClient().query_logs(state["alert"].get("service", ""))
    return {"logs": logs}


async def _fetch_metrics(state: RCAState) -> RCAState:
    metrics = await MonitoringClient().query_metrics(state["alert"].get("service", ""))
    return {"metrics": metrics}


async def _fetch_deploys(state: RCAState) -> RCAState:
    repo = get_settings().deploy_repo
    if repo and "/" in repo:
        owner, name = repo.split("/", 1)
        try:
            commits = await GitHubClient().list_recent_commits(owner, name, limit=10)
            text = "\n".join(f"{c['sha']} {c['message']} ({c['author']} {c['date']})" for c in commits)
            return {"deploys": text or "（无近期提交）"}
        except Exception as exc:  # noqa: BLE001 - 降级为 mock
            logger.warning("deploys_fetch_failed", error=str(exc))
    # mock 近期发布
    return {
        "deploys": (
            "a1b2c3d feat: 调整连接池上限从 200 降到 50 (2h ago)\n"
            "e4f5g6h chore: 升级依赖 (6h ago)\n"
            "i7j8k9l fix: 修复缓存击穿 (1d ago)"
        )
    }


# ---------- 分析 / 验证 / 收敛 ----------


def _context_block(state: RCAState) -> str:
    return (
        f"【告警】\n{format_alert(state['alert'])}\n\n"
        f"【错误日志】\n{state.get('logs', '')}\n\n"
        f"【监控指标】\n{state.get('metrics', '')}\n\n"
        f"【近期发布】\n{state.get('deploys', '')}"
    )


async def _analyze(state: RCAState) -> RCAState:
    iteration = state.get("iteration", 0) + 1
    feedback = ""
    if state.get("verification"):
        feedback = (
            f"\n\n【上一轮验证反馈】{state['verification'].get('reasoning', '')}"
            "（请据此修正或提出新的假设）"
        )
    result = await llm_json(ANALYZE_SYSTEM, _context_block(state) + feedback)
    hypotheses = result if isinstance(result, list) else []
    logger.info("rca_analyze", iteration=iteration, hypotheses=len(hypotheses))
    return {"hypotheses": hypotheses, "iteration": iteration}


async def _verify(state: RCAState) -> RCAState:
    hypotheses = state.get("hypotheses") or []
    if not hypotheses:
        return {"verification": {"confirmed": False, "reasoning": "无可验证假设"}, "confidence": 0.0}

    top = hypotheses[0]
    user = (
        f"{_context_block(state)}\n\n【待验证假设】\n{top.get('cause', '')}\n"
        f"【需验证点】{top.get('evidence_to_check', '')}"
    )
    result = await llm_json(VERIFY_SYSTEM, user)
    result = result if isinstance(result, dict) else {}
    confidence = float(result.get("confidence", 0.0) or 0.0)
    logger.info("rca_verify", confirmed=result.get("confirmed"), confidence=confidence)
    return {"verification": result, "confidence": confidence}


def _route_after_verify(state: RCAState) -> str:
    settings = get_settings()
    if (
        state.get("confidence", 0.0) >= settings.rca_confidence_threshold
        or state.get("iteration", 0) >= settings.rca_max_iterations
    ):
        return "conclude"
    return "analyze"


async def _conclude(state: RCAState) -> RCAState:
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
    return {"conclusion": str(resp.content)}


@lru_cache(maxsize=1)
def build_rca_agent():
    """构建并编译告警根因工作流。"""
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
