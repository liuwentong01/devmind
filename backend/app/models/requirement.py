"""需求拆解人工确认状态的持久化 ORM 模型。

调用链：``/requirement/plan`` 创建 pending 记录，``/confirm`` 通过 requirement store 原子
认领为 processing、逐条记录 Jira progress，最后保存 completed 结果。运维可按
``status``/``expires_at`` 索引清理过期计划。

生产边界：本表提供多副本租约和重试恢复，但无法让 PostgreSQL 与 Jira 形成分布式事务；
因此每个外部 issue 必须携带稳定幂等标识并即时 checkpoint，不能删掉 progress 字段。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RequirementPlan(Base):
    """跨进程可恢复的需求拆解计划。

    ``lease_token`` 用于确认阶段的 compare-and-set，避免两个 API 副本同时向 Jira
    写入同一批任务。外部调用期间不持有数据库事务或行锁：网络请求可能很慢，持锁会放大
    连接池占用和死锁风险；改用短事务 CAS + progress checkpoint 恢复。
    """

    __tablename__ = "requirement_plans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed')",
            name="ck_requirement_plans_status",
        ),
        Index("ix_requirement_plans_status_expires", "status", "expires_at"),
    )

    thread_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    epic: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    tasks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    progress: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    approved_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
