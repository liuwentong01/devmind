"""代码理解 API：索引仓库 + 代码问答(SSE) + 影响分析。"""

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.agents.code_understanding.agent import analyze_impact
from app.agents.code_understanding.graph import build_code_qa_agent
from app.core.logging import get_logger
from app.kernel.graph.indexer import index_repo
from app.schemas.codebase import (
    CodeQueryRequest,
    ImpactRequest,
    IndexRequest,
    IndexResponse,
)

logger = get_logger("gateway.codebase")
router = APIRouter(prefix="/codebase", tags=["codebase"])


@router.post("/index", response_model=IndexResponse)
async def index(req: IndexRequest) -> IndexResponse:
    """索引本地仓库目录，构建代码图谱。"""
    try:
        stats = await index_repo(req.repo, req.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - Neo4j 不可用等
        raise HTTPException(status_code=503, detail=f"索引失败：{exc}") from exc
    return IndexResponse(repo=req.repo, **stats)


async def _stream_qa(repo: str, question: str) -> AsyncGenerator[dict, None]:
    agent = build_code_qa_agent()
    inputs = {"repo": repo, "question": question}
    try:
        async for mode, data in agent.astream(inputs, stream_mode=["updates", "messages"]):
            if mode == "updates":
                located = data.get("locate")
                if located and "context" in located:
                    files = sorted({c["path"] for c in located["context"]})
                    yield {"event": "context", "data": json.dumps({"files": files}, ensure_ascii=False)}
            elif mode == "messages":
                msg_chunk, metadata = data
                if metadata.get("langgraph_node") != "answer":
                    continue
                text = getattr(msg_chunk, "content", "") or ""
                if text:
                    yield {"event": "token", "data": text}
        yield {"event": "done", "data": "[DONE]"}
    except Exception as exc:  # noqa: BLE001
        logger.error("code_qa_stream_error", error=str(exc))
        yield {"event": "error", "data": json.dumps({"message": str(exc)}, ensure_ascii=False)}


@router.post("/query/stream")
async def query_stream(req: CodeQueryRequest) -> EventSourceResponse:
    """代码库问答（SSE：先回相关文件，再流式回答）。"""
    return EventSourceResponse(_stream_qa(req.repo, req.question))


@router.post("/impact")
async def impact(req: ImpactRequest) -> dict:
    """改动影响分析：返回调用者 / 被调用者 + LLM 影响总结。"""
    try:
        return await analyze_impact(req.repo, req.function, depth=req.depth)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"分析失败：{exc}") from exc
