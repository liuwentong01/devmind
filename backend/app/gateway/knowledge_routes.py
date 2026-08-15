"""知识库 API：文档入库/列表/删除与 RAG 流式问答。

调用链：``POST /api/knowledge/documents`` 分块、Embedding 并在同一数据库事务落库；
``POST /api/knowledge/query/stream`` 召回切片后先发 ``sources``，再发回答 ``token``。入库示例：
``curl -X POST -H 'Content-Type: application/json' -d
'{"title":"发布规范","content":"发布需审批","source":"handbook"}' .../api/knowledge/documents``。

生产边界：Embedding 是外部调用，失败时整次入库回滚；长正文受 Schema/请求体双重限制。
``source`` 当前用于检索来源隔离而非用户 ACL，企业多租户上线前必须把身份/租户条件下推到
入库、列表、删除和检索，不能只在前端隐藏文档。
"""

import json
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.agents.knowledge_qa.graph import build_knowledge_agent
from app.core.db import SessionLocal, get_session
from app.core.logging import get_logger
from app.gateway.sse import stream_response
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
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/documents", response_model=IngestResponse)
async def create_document(req: IngestRequest, session: SessionDep) -> IngestResponse:
    """执行“分块 → 向量化 → 落库”，全部成功后显式提交事务。"""
    try:
        doc = await ingest_document(session, req.title, req.content, req.source)
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        logger.exception("doc_ingest_failed")
        raise HTTPException(status_code=503, detail="文档暂时无法入库") from exc
    chunk_count = await session.scalar(
        select(func.count(Chunk.id)).where(Chunk.document_id == doc.id)
    )
    return IngestResponse(id=doc.id, title=doc.title, chunk_count=chunk_count or 0)


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    session: SessionDep,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[DocumentOut]:
    """按创建时间倒序分页列出文档及切片数量；最多一次返回 200 条。"""
    stmt = (
        select(Document, func.count(Chunk.id))
        .outerjoin(Chunk, Chunk.document_id == Document.id)
        .group_by(Document.id)
        .order_by(Document.created_at.desc(), Document.id.desc())
        .offset(offset)
        .limit(limit)
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
async def delete_document(doc_id: int, session: SessionDep) -> None:
    """删除文档并由外键级联删除切片；不存在时返回 404。"""
    result = await session.execute(delete(Document).where(Document.id == doc_id))
    if result.rowcount == 0:
        await session.rollback()
        raise HTTPException(status_code=404, detail="文档不存在")
    try:
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        logger.exception("doc_delete_failed", doc_id=doc_id)
        raise HTTPException(status_code=503, detail="文档暂时无法删除") from exc


async def _stream_qa(req: QueryRequest) -> AsyncGenerator[dict, None]:
    """运行知识答疑工作流，转成 SSE 事件流。

    通过 stream_mode=["updates", "messages"] 同时获取：
    - updates：retrieve 节点产出的来源列表
    - messages：generate 节点的流式 token
    """
    agent = build_knowledge_agent()
    # 路由函数返回 EventSourceResponse 后，普通 FastAPI dependency 可能结束；实际 Agent
    # 仍会继续迭代流。因此会话必须在生成器内部创建并覆盖整个 SSE 生命周期。
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
        except Exception:  # noqa: BLE001
            logger.exception("qa_stream_error")
            yield {
                "event": "error",
                "data": json.dumps({"message": "知识问答失败，请稍后重试"}, ensure_ascii=False),
            }


@router.post("/query/stream")
async def query_stream(req: QueryRequest) -> EventSourceResponse:
    """RAG SSE 问答：依次返回 ``sources``、若干 ``token``、最后 ``done``。"""
    return stream_response(_stream_qa(req))
