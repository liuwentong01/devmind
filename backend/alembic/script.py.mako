"""${message}

这是由 ``alembic revision -m "说明"`` 生成的迁移文件。实现 ``upgrade`` 描述前向结构变化，
实现 ``downgrade`` 描述可接受的回滚；在提交前应同时审阅两条路径和生成 SQL
（``alembic upgrade head --sql``）。生产环境由独立迁移 Job 执行，不在 API 启动时运行。

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    """应用本 revision 的前向数据库变化。"""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """回滚本 revision；若不可逆，应明确记录数据恢复方式。"""
    ${downgrades if downgrades else "pass"}
