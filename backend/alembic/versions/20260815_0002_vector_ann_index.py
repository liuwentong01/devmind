"""以 PostgreSQL 并发方式构建 pgvector HNSW 近似最近邻索引。

本 revision 刻意与事务型 DDL 分离；执行 ``alembic upgrade head`` 时会先并发删除可能残留的
同名无效索引，再以 cosine operator class 重建。降级只删除索引，不删除向量数据。

生产边界：``CREATE/DROP INDEX CONCURRENTLY`` 不能位于普通事务内，因此必须使用
``autocommit_block``。失败的并发创建可能留下 invalid index，重试前的 DROP 正是恢复步骤；
大表仍会消耗 CPU/IO 和额外磁盘，发布前应评估容量并监控 ``pg_stat_progress_create_index``。

Revision ID: 20260815_0002
Revises: 20260815_0001
Create Date: 2026-08-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260815_0002"
down_revision: str | None = "20260815_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """在不长时间阻塞知识写入的前提下重建 cosine HNSW 索引。"""
    # Alembic 默认把 revision 包在事务中，而 PostgreSQL 的 CONCURRENTLY 明确禁止这一点。
    # 使用 autocommit block 是数据库语义要求，不是为了提升速度。
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_chunks_embedding_hnsw")
        op.execute(
            "CREATE INDEX CONCURRENTLY ix_chunks_embedding_hnsw "
            "ON chunks USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    """并发删除 HNSW 索引；查询仍正确，但会退化为更昂贵的扫描。"""
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_chunks_embedding_hnsw")
