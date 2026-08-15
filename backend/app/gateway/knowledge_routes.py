"""知识库 API：文档管理 + RAG 流式问答。"""

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.agents.knowledge_qa.graph import build_knowledge_agent
from app.core.db import SessionLocal, get_session
from app.core.logging import get_logger
from app.kernel.rag.retriever import ingest_document
from app.models.knowledge import Chunk, Document
from app.schemas.knowledge import (
    DocumentOut,
    IngestRequest,
    IngestResponse,
    QueryRequest,
)

logger = get_logger("gateway.knowledge")
router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/documents", response_model=IngestResponse)
async def create_document(
    req: IngestRequest, session: AsyncSession = Depends(get_session)
) -> IngestResponse:
    """文档入库：分块 → 向量化 → 存储。"""
    try:
        doc = await ingest_document(session, req.title, req.content, req.source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    chunk_count = await session.scalar(select(func.count(Chunk.id)).where(Chunk.document_id == doc.id))
    return IngestResponse(id=doc.id, title=doc.title, chunk_count=chunk_count or 0)


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(session: AsyncSession = Depends(get_session)) -> list[DocumentOut]:
    """文档列表（含切片数量）。"""
    stmt = (
        select(Document, func.count(Chunk.id))
        .outerjoin(Chunk, Chunk.document_id == Document.id)
        .group_by(Document.id)
        .order_by(Document.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        DocumentOut(
            id=doc.id,
            title=doc.title,
            source=doc.source,
            chunk_count=count,
            created_at=doc.created_at,
        )
        for doc, count in rows
    ]


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: int, session: AsyncSession = Depends(get_session)) -> None:
    """删除文档（级联删除其切片）。"""
    result = await session.execute(delete(Document).where(Document.id == doc_id))
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="文档不存在")


async def _stream_qa(req: QueryRequest) -> AsyncGenerator[dict, None]:
    """运行知识答疑工作流，转成 SSE 事件流。

    通过 stream_mode=["updates", "messages"] 同时获取：
    - updates：retrieve 节点产出的来源列表
    - messages：generate 节点的流式 token
    """
    agent = build_knowledge_agent()
    # SSE 期间需要持有会话，故在生成器内自行管理生命周期
    async with SessionLocal() as session:
        inputs = {"question": req.question, "session": session, "top_k": req.top_k}
        try:
            async for mode, data in agent.astream(inputs, stream_mode=["updates", "messages"]):
                if mode == "updates":
                    node_update = data.get("retrieve")
                    if node_update and "sources" in node_update:
                        yield {
                            "event": "sources",
                            "data": json.dumps(node_update["sources"], ensure_ascii=False),
                        }
                elif mode == "messages":
                    msg_chunk, metadata = data
                    if metadata.get("langgraph_node") != "generate":
                        continue
                    text = getattr(msg_chunk, "content", "") or ""
                    if text:
                        yield {"event": "token", "data": text}

            yield {"event": "done", "data": "[DONE]"}
        except Exception as exc:  # noqa: BLE001
            logger.error("qa_stream_error", error=str(exc))
            yield {"event": "error", "data": json.dumps({"message": str(exc)}, ensure_ascii=False)}


@router.post("/query/stream")
async def query_stream(req: QueryRequest) -> EventSourceResponse:
    """RAG 流式问答接口（SSE）：先返回 sources，再流式返回 token。"""
    return EventSourceResponse(_stream_qa(req))
