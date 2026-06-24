"""检索器：文档入库（分块→向量化→存储）+ 向量相似度检索。"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.kernel.rag.chunking import split_text
from app.kernel.rag.embeddings import embed_query, embed_texts
from app.models.knowledge import Chunk, Document

logger = get_logger("rag.retriever")


@dataclass
class RetrievedChunk:
    """一条检索结果（带来源信息，用于答案引用）。"""

    chunk_id: int
    document_id: int
    document_title: str
    content: str
    score: float  # 余弦相似度，越大越相关


async def ingest_document(session: AsyncSession, title: str, content: str, source: str = "manual") -> Document:
    """把一篇文档分块、向量化并写入库。

    Returns:
        已落库的 Document（含切片数量可通过 chunks 关系获取）。
    """
    chunks = split_text(content)
    if not chunks:
        raise ValueError("文档内容为空，无法入库")

    vectors = await embed_texts(chunks)

    doc = Document(title=title, content=content, source=source)
    doc.chunks = [
        Chunk(chunk_index=i, content=text, embedding=vector)
        for i, (text, vector) in enumerate(zip(chunks, vectors, strict=True))
    ]
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    logger.info("doc_ingested", doc_id=doc.id, chunks=len(chunks))
    return doc


async def retrieve(session: AsyncSession, query: str, top_k: int | None = None) -> list[RetrievedChunk]:
    """对查询做向量检索，返回最相关的若干切片。

    用 pgvector 的余弦距离排序（cosine_distance = 1 - 余弦相似度）。
    """
    k = top_k or get_settings().rag_top_k
    qvec = await embed_query(query)

    distance = Chunk.embedding.cosine_distance(qvec)
    stmt = (
        select(Chunk, Document.title, distance.label("distance"))
        .join(Document, Chunk.document_id == Document.id)
        .order_by(distance.asc())
        .limit(k)
    )
    rows = (await session.execute(stmt)).all()

    results = [
        RetrievedChunk(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            document_title=title,
            content=chunk.content,
            score=round(1.0 - float(dist), 4),
        )
        for chunk, title, dist in rows
    ]
    logger.info("retrieve_done", query_len=len(query), hits=len(results))
    return results
