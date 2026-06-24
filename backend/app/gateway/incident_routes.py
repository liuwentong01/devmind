"""告警根因 API：手动流式根因分析（SSE）。

通过 stream_mode=["updates","messages"] 把各节点进度（拉取/分析/验证）转成 step 事件，
把 conclude 节点的最终报告 token 流式推给前端。
"""

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.agents.incident_rca.graph import build_rca_agent
from app.core.logging import get_logger
from app.schemas.incident import AlertInput

logger = get_logger("gateway.incident")
router = APIRouter(prefix="/incident", tags=["incident"])

# 节点 → 前端展示的步骤文案
STEP_LABELS = {
    "fetch_logs": "拉取错误日志",
    "fetch_metrics": "拉取监控指标",
    "fetch_deploys": "拉取近期发布",
    "analyze": "关联分析、生成假设",
    "verify": "验证假设",
    "conclude": "生成根因报告",
}


async def _stream_rca(alert: dict) -> AsyncGenerator[dict, None]:
    agent = build_rca_agent()
    inputs = {"alert": alert, "iteration": 0}
    try:
        async for mode, data in agent.astream(inputs, stream_mode=["updates", "messages"]):
            if mode == "updates":
                for node, update in data.items():
                    detail = ""
                    if node == "analyze" and isinstance(update, dict):
                        hyps = update.get("hypotheses") or []
                        detail = f"生成 {len(hyps)} 个假设（第 {update.get('iteration')} 轮）"
                    elif node == "verify" and isinstance(update, dict):
                        conf = update.get("confidence", 0.0)
                        detail = f"置信度 {conf}"
                    yield {
                        "event": "step",
                        "data": json.dumps(
                            {"node": node, "label": STEP_LABELS.get(node, node), "detail": detail},
                            ensure_ascii=False,
                        ),
                    }
            elif mode == "messages":
                msg_chunk, metadata = data
                if metadata.get("langgraph_node") != "conclude":
                    continue
                text = getattr(msg_chunk, "content", "") or ""
                if text:
                    yield {"event": "token", "data": text}

        yield {"event": "done", "data": "[DONE]"}
    except Exception as exc:  # noqa: BLE001
        logger.error("rca_stream_error", error=str(exc))
        yield {"event": "error", "data": json.dumps({"message": str(exc)}, ensure_ascii=False)}


@router.post("/analyze/stream")
async def analyze_stream(alert: AlertInput) -> EventSourceResponse:
    """手动触发根因分析，流式返回步骤进度 + 最终报告。"""
    return EventSourceResponse(_stream_rca(alert.to_dict()))
