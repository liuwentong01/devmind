"""强化知识库约束，并加入可恢复的需求确认计划表。

升级内容：禁止同一文档重复 chunk 序号、为文档来源建索引，并创建带状态/过期索引的
``requirement_plans``。运行方式：在 ``backend`` 目录执行 ``alembic upgrade
20260815_0001``，通常发布时直接使用 ``alembic upgrade head``。

生产边界：添加唯一约束前必须确认历史库不存在重复 ``(document_id, chunk_index)``；否则
迁移会失败，需先人工清理数据。降级会删除全部待确认/已完成计划，且可能让应用失去幂等
恢复依据，应先停写、备份并确认旧版本应用兼容。

Revision ID: 20260815_0001
Revises: 20260815_0000
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260815_0001"
down_revision: str | None = "20260815_0000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """添加知识表索引/唯一约束，并创建 durable HITL 状态表。"""
    op.create_unique_constraint(
        "uq_chunks_document_index", "chunks", ["document_id", "chunk_index"]
    )
    op.create_index("ix_documents_source", "documents", ["source"], unique=False)

    op.create_table(
        "requirement_plans",
        sa.Column("thread_id", sa.String(length=32), nullable=False),
        sa.Column("project", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("epic", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("tasks", sa.JSON(), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("approved_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("lease_token", sa.String(length=32), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed')",
            name="ck_requirement_plans_status",
        ),
        sa.PrimaryKeyConstraint("thread_id"),
    )
    op.create_index(
        "ix_requirement_plans_status_expires",
        "requirement_plans",
        ["status", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """移除需求计划表及知识表强化约束，数据回滚不可自动恢复。"""
    op.drop_index("ix_requirement_plans_status_expires", table_name="requirement_plans")
    op.drop_table("requirement_plans")
    op.drop_index("ix_documents_source", table_name="documents")
    op.drop_constraint("uq_chunks_document_index", "chunks", type_="unique")
