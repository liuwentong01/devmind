"""手动告警根因分析 API，把 RCA LangGraph 执行过程转换为 SSE。

调用链：``POST /api/incident/analyze/stream`` → 拉日志/指标/发布 → 生成并验证假设 → conclude。
请求示例：``{"alertname":"HighErrorRate","service":"checkout","severity":"critical"}``。
``updates`` 产生 ``step`` 进度，``messages`` 产生 conclude 的 ``token``，最后发送 ``done``。

生产边界：报告是辅助诊断而非自动事实；证据降级、未确认或低置信度时必须把 warning 放在
结论前。Loki/Prometheus/GitHub 证据都有大小与迭代上限，生产禁止 mock 证据。接口只分析，
不会自动执行回滚、变更生产或关闭告警。
"""

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.agents.incident_rca.graph import _conclusion_warning, build_rca_agent
from app.core.logging import get_logger
from app.gateway.sse import stream_response
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
    """运行 RCA 图并生成 ``step/token/done/error`` 事件。

    LangGraph 版本/模型可能只在 updates 中给完整 conclusion，也可能在 messages 中流 token，
    因而同时支持两条输出路径，并用两个标志避免警告或完整结论重复发送。
    """
    agent = build_rca_agent()
    inputs = {"alert": alert, "iteration": 0}
    streamed_state = dict(inputs)
    warning_sent = False
    conclusion_content_sent = False
    try:
        async for mode, data in agent.astream(inputs, stream_mode=["updates", "messages"]):
            if mode == "updates":
                for node, update in data.items():
                    if isinstance(update, dict):
                        streamed_state.update(update)
                    # 警告必须先于任何结论 token 到达；否则 UI 已展示“确定根因”后再追加风险
                    # 提示，会让操作者产生错误的确定性印象。
                    if node == "conclude" and not warning_sent:
                        warning = _conclusion_warning(streamed_state)
                        if warning:
                            yield {"event": "token", "data": warning}
                        warning_sent = True
                    if (
                        node == "conclude"
                        and not conclusion_content_sent
                        and isinstance(update, dict)
                    ):
                        final_text = str(update.get("conclusion") or "")
                        warning = _conclusion_warning(streamed_state)
                        if warning and final_text.startswith(warning):
                            final_text = final_text[len(warning) :]
                        if final_text:
                            yield {"event": "token", "data": final_text}
                            conclusion_content_sent = True
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
                    if not warning_sent:
                        warning = _conclusion_warning(streamed_state)
                        if warning:
                            yield {"event": "token", "data": warning}
                        warning_sent = True
                    yield {"event": "token", "data": text}
                    conclusion_content_sent = True

        yield {"event": "done", "data": "[DONE]"}
    except Exception:  # noqa: BLE001
        logger.exception("rca_stream_error")
        yield {
            "event": "error",
            "data": json.dumps({"message": "根因分析失败，请稍后重试"}, ensure_ascii=False),
        }


@router.post("/analyze/stream")
async def analyze_stream(alert: AlertInput) -> EventSourceResponse:
    """手动触发根因分析，流式返回步骤、风险提示与最终报告。"""
    return stream_response(_stream_rca(alert.to_dict()))
