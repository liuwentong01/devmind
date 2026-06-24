"""代码审查 API：手动流式审查（SSE）+ 触发异步 PR 审查（入队）。"""

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.agents.code_review.graph import build_review_agent
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.kernel.queue import get_queue
from app.schemas.review import ReviewDiffRequest, TriggerReviewRequest

logger = get_logger("gateway.review")
router = APIRouter(prefix="/review", tags=["review"])


async def _stream_review(diff: str) -> AsyncGenerator[dict, None]:
    """运行审查工作流，转成 SSE 事件流（rules + token）。"""
    agent = build_review_agent()
    async with SessionLocal() as session:
        inputs = {"diff": diff, "session": session}
        try:
            async for mode, data in agent.astream(inputs, stream_mode=["updates", "messages"]):
                if mode == "updates":
                    node_update = data.get("load_rules")
                    if node_update and "rules" in node_update:
                        yield {
                            "event": "rules",
                            "data": json.dumps(
                                {"count": len(node_update["rules"])}, ensure_ascii=False
                            ),
                        }
                elif mode == "messages":
                    msg_chunk, metadata = data
                    if metadata.get("langgraph_node") != "review":
                        continue
                    text = getattr(msg_chunk, "content", "") or ""
                    if text:
                        yield {"event": "token", "data": text}

            yield {"event": "done", "data": "[DONE]"}
        except Exception as exc:  # noqa: BLE001
            logger.error("review_stream_error", error=str(exc))
            yield {"event": "error", "data": json.dumps({"message": str(exc)}, ensure_ascii=False)}


@router.post("/stream")
async def review_stream(req: ReviewDiffRequest) -> EventSourceResponse:
    """手动流式审查：前端粘贴 diff，实时返回审查意见。"""
    return EventSourceResponse(_stream_review(req.diff))


@router.post("/trigger", status_code=202)
async def trigger_review(req: TriggerReviewRequest) -> dict:
    """手动触发对某 PR 的异步审查（入 ARQ 队列，由 worker 拉 diff 并回写）。"""
    try:
        queue = await get_queue()
        job = await queue.enqueue_job("review_pull_task", req.owner, req.repo, req.number)
    except Exception as exc:  # noqa: BLE001 - Redis 不可用时给出明确提示
        raise HTTPException(status_code=503, detail=f"队列不可用：{exc}") from exc
    return {"status": "queued", "job_id": job.job_id if job else None}
