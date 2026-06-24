"""数据库：异步 SQLAlchemy 引擎、会话、初始化。"""

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("db")

_settings = get_settings()

# echo=False；如需调试 SQL 可设为 True
engine = create_async_engine(_settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：提供一个数据库会话。"""
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """启动时初始化：启用 pgvector 扩展并建表。

    P1 用 create_all 简化建表；生产应改用 Alembic 迁移。
    """
    # 确保模型已被导入注册到 Base.metadata
    from app.models import knowledge  # noqa: F401

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    logger.info("db_initialized")
