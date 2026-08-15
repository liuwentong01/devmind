"""知识库的 SQLAlchemy ORM 模型：原文 ``Document`` 与向量切片 ``Chunk``。

调用链：gateway 调用 ``ingest_document`` 创建文档并分块；retriever 对 ``Chunk.embedding``
做余弦近邻检索，再通过 relationship 回到来源文档。查询示例：
``select(Chunk).where(Chunk.document_id == document_id)``。

生产边界：Embedding 维度在模块导入时由配置确定，必须与数据库列和当前模型一致；改变维度
需要迁移并重建全部向量。HNSW 的物理创建由 Alembic 并发迁移完成，不能只依赖 metadata。
"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.core.db import Base

_EMBEDDING_DIM = get_settings().embedding_dim


class Document(Base):
    """一篇知识原文；删除时数据库级联删除它的全部切片。"""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[str] = mapped_column(String(512), default="manual", index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Chunk(Base):
    """文档切片与向量，是 RAG 召回和引用来源的最小单位。

    ``(document_id, chunk_index)`` 唯一约束防止同一入库流程重复落相同位置；HNSW 使用
    ``vector_cosine_ops``，因此查询端也必须使用余弦距离才能命中该索引。
    """

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunks_document_index"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(_EMBEDDING_DIM))

    document: Mapped["Document"] = relationship(back_populates="chunks")
