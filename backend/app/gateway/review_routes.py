"""代码审查 API：手动 diff SSE 审查与 GitHub PR 异步审查入队。

调用链：``POST /api/review/stream`` 直接运行“加载团队规则 → LLM 审查”，返回
``rules/token/done``；``POST /api/review/trigger`` 先读取 PR 当前 head SHA，再把稳定 job id 的
``review_pull_task`` 放入 ARQ，由 worker 拉 diff、复核 SHA 并回写。触发示例：
``{"owner":"acme","repo":"service","number":12}``。

生产边界：diff 和规则均是不可信模型上下文；手动流只返回建议，不回写 GitHub。异步入口的
去重以 owner/repo/PR/head SHA 为粒度，避免 webhook 与手动入口重复执行，但 Redis 任务去重和
GitHub 评论标记仍是跨系统尽力幂等，操作者应保留重放/审计手段。
"""

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.agents.code_review.graph import build_review_agent
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.gateway.sse import stream_response
from app.integrations.github import GitHubClient
from app.kernel.queue import get_queue, review_job_id
from app.schemas.review import ReviewDiffRequest, TriggerReviewRequest

logger = get_logger("gateway.review")
router = APIRouter(prefix="/review", tags=["review"])


async def _stream_review(diff: str) -> AsyncGenerator[dict, None]:
    """运行审查图，将规则数量与 review 节点 token 转成 SSE 事件。"""
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
        except Exception:  # noqa: BLE001
            logger.exception("review_stream_error")
            yield {
                "event": "error",
                "data": json.dumps({"message": "代码审查失败，请稍后重试"}, ensure_ascii=False),
            }


@router.post("/stream")
async def review_stream(req: ReviewDiffRequest) -> EventSourceResponse:
    """手动流式审查粘贴的 diff；不读取仓库也不向 GitHub 写评论。"""
    return stream_response(_stream_review(req.diff))


@router.post("/trigger", status_code=202)
async def trigger_review(req: TriggerReviewRequest) -> dict:
    """固定 PR head SHA 后入 ARQ；重复 head 返回 ``status=duplicate``。"""
    # 先绑定当前 head SHA；否则同一 PR 更新后仍会命中旧 job id，或 worker 审到
    # 入队后才出现的新版本。
    try:
        head_sha = await GitHubClient().get_pull_head_sha(req.owner, req.repo, req.number)
    except Exception as exc:  # noqa: BLE001
        logger.exception("review_head_lookup_failed")
        raise HTTPException(status_code=502, detail="暂时无法读取 GitHub PR") from exc

    try:
        queue = await get_queue()
        # 与 GitHub webhook 使用完全相同的 key，避免同一 head 被两条入口并发执行。
        job_id = review_job_id(req.owner, req.repo, req.number, head_sha)
        job = await queue.enqueue_job(
            "review_pull_task",
            req.owner,
            req.repo,
            req.number,
            head_sha,
            _job_id=job_id,
            _expires=86_400,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("review_enqueue_failed")
        raise HTTPException(status_code=503, detail="任务队列暂时不可用") from exc
    return {
        "status": "queued" if job else "duplicate",
        "job_id": job.job_id if job else job_id,
    }
