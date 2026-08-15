"""建立知识库基线表，作为 DevMind 的首个 Alembic revision。

升级会确保 pgvector extension 存在，并创建 ``documents``、``chunks`` 与外键索引；降级会
删除这两张表及其数据。全新数据库在 ``backend`` 目录运行 ``alembic upgrade head`` 即可。

生产边界：早期由 ``Base.metadata.create_all`` 初始化且已存在这两张表的部署，不能直接执行
本 revision；应先核对结构/备份，再执行 ``alembic stamp 20260815_0000`` 后继续升级。
extension 的创建通常需要 DBA 权限，受管数据库可由 DBA 预先创建。降级具有数据破坏性，
只能在确认备份和回滚窗口后执行。

Revision ID: 20260815_0000
Revises:
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "20260815_0000"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """启用 pgvector，并创建原文与 1024 维切片向量基线表。"""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("source", sa.String(length=512), nullable=False, server_default="manual"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(dim=1024), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"], unique=False)


def downgrade() -> None:
    """删除知识库基线表；会级联丢失文档和向量数据。"""
    op.drop_index("ix_chunks_document_id", table_name="chunks")
    op.drop_table("chunks")
    op.drop_table("documents")
