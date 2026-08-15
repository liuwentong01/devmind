"""RAG 检索器：文档入库与 pgvector 余弦近邻查询。

架构位置：文档 API 调用 ``ingest_document`` 完成“分块 → document Embedding → ORM 建模”；
知识问答和代码审查调用 ``retrieve`` 完成“query Embedding → 向量排序 → 来源封装”。本层
衔接外部 Embedding 服务和 PostgreSQL，但把事务提交权留给 API/服务编排层。

典型调用示例::

    async with session_factory() as session:
        doc = await ingest_document(session, "发布手册", text, source="manual")
        await session.commit()
        hits = await retrieve(session, "如何回滚？", top_k=5, source="manual")

失败与一致性：空文档、Embedding 数量/维度异常会在写库前失败；``flush`` 后若上层操作失败，
调用方可统一 rollback，避免文档半提交。检索只读但会产生一次 Query Embedding 外部调用。
当前相似度分数是 ``1 - cosine_distance``，适合排序而非概率解释；没有阈值、重排、关键词
混合检索、文档 ACL 或时效过滤，这些属于生产数据治理和未来路线。
"""

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
    """带来源的不可变语义载体（字段本身未冻结），用于生成回答引用。

    ``score`` 是 pgvector 余弦相似度的四位小数表示，越大通常越相关，但不保证落在某个
    业务概率区间，也不能单独用作事实可信度。
    """

    chunk_id: int
    document_id: int
    document_title: str
    content: str
    score: float  # 余弦相似度，越大越相关


async def ingest_document(
    session: AsyncSession, title: str, content: str, source: str = "manual"
) -> Document:
    """把一篇文档分块、向量化并写入库。

    Returns:
        已落库的 Document（含切片数量可通过 chunks 关系获取）。

    Raises:
        ValueError: 文档为空，或 Embedding 服务返回数量/维度/数值异常。
        sqlalchemy.exc.SQLAlchemyError: ``flush``/``refresh`` 失败。

    注意本函数只 ``flush`` 不 ``commit``。示例中的调用方显式提交；若还需写审计记录，
    可以在同一事务完成后一次提交，任何步骤失败都能整体回滚。
    """
    chunks = split_text(content)
    if not chunks:
        raise ValueError("文档内容为空，无法入库")

    vectors = await embed_texts(chunks)

    doc = Document(title=title, content=content, source=source)
    # ``strict=True`` 是第二道长度一致性保护：若未来 embedding 实现回归，不允许 zip 静默
    # 丢掉文档尾部切片并形成“看似成功但知识不完整”的记录。
    doc.chunks = [
        Chunk(chunk_index=i, content=text, embedding=vector)
        for i, (text, vector) in enumerate(zip(chunks, vectors, strict=True))
    ]
    session.add(doc)
    # 事务边界归 API/调用方所有；服务层只 flush，避免后续操作失败时出现半提交。
    await session.flush()
    await session.refresh(doc)
    logger.info("doc_ingested", doc_id=doc.id, chunks=len(chunks))
    return doc


async def retrieve(
    session: AsyncSession,
    query: str,
    top_k: int | None = None,
    *,
    source: str | None = None,
) -> list[RetrievedChunk]:
    """对查询做向量检索，返回最相关的若干切片。

    用 pgvector 的余弦距离排序（``cosine_distance = 1 - cosine_similarity``）。

    Args:
        session: 请求级异步 SQLAlchemy 会话。
        query: 用户问题或待审查 diff 摘要。
        top_k: 召回上限；省略时使用配置，显式值必须为 1..100。
        source: 可选的严格来源过滤，例如只查团队审查规范。

    Returns:
        按余弦距离升序（相似度降序）排列的来源切片。

    ``source`` 条件在数据库查询中执行，而不是召回后用 Python 过滤；这样不会让其他来源
    占满 top-k 并造成目标集合假性空召回，也是未来接入 ACL 时应遵循的模式。
    """
    k = top_k or get_settings().rag_top_k
    if not 1 <= k <= 100:
        raise ValueError("top_k 必须在 1 到 100 之间")
    qvec = await embed_query(query)

    distance = Chunk.embedding.cosine_distance(qvec)
    stmt = (
        select(Chunk, Document.title, distance.label("distance"))
        .join(Document, Chunk.document_id == Document.id)
        .order_by(distance.asc())
        .limit(k)
    )
    if source is not None:
        stmt = stmt.where(Document.source == source)
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
    logger.info("retrieve_done", query_len=len(query), source=source, hits=len(results))
    return results
