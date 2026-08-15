"""PostgreSQL 异步引擎、会话依赖、启动校验与关闭生命周期。

调用链：模块加载时依据 ``Settings`` 创建惰性连接池；FastAPI lifespan 调用 ``init_db``，
路由通过 ``Depends(get_session)`` 获取请求级会话，readiness 调用 ``check_db``，关机调用
``close_db``。非 Web 场景示例：``async with SessionLocal() as s: await s.execute(stmt)``。

生产边界：非本地进程绝不自动改 schema，只验证 Alembic head 和向量维度；迁移由独立 Job
使用 DDL 凭据执行。会话依赖只在异常时自动回滚，成功路径是否 commit 由业务函数明确决定，
因此写操作不能假定依赖会自动提交。
"""

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("db")

_settings = get_settings()
REQUIRED_DB_REVISION = "20260815_0002"

# 有界连接池避免数据库故障/突发流量下无限等待；pre-ping + recycle 处理云数据库和
# LB 主动关闭空闲连接的常见场景。
engine = create_async_engine(
    _settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    pool_timeout=_settings.db_pool_timeout,
    pool_recycle=_settings.db_pool_recycle,
    pool_use_lifo=True,
    connect_args={"connect_timeout": int(_settings.db_connect_timeout)},
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """提供请求级 ``AsyncSession``，异常/取消时回滚，离开请求时归还连接。"""
    async with SessionLocal() as session:
        try:
            yield session
        except BaseException:
            # 显式回滚，确保同一连接归还连接池前没有悬挂事务。也不能吞掉
            # CancelledError（例如 SSE 客户端断开）。
            await session.rollback()
            raise


async def init_db() -> None:
    """本地创建开发 schema；非本地仅验证 revision 与向量列维度。"""
    # 确保模型已被导入注册到 Base.metadata。create_all 仅作为本地开发兜底；
    # 生产发布应在启动应用前执行 ``alembic upgrade head``。
    from app.models import knowledge, requirement  # noqa: F401

    if _settings.is_local:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
    else:
        # 非本地环境禁止应用进程隐式改 schema；发布流水线必须先跑 Alembic。
        async with engine.connect() as conn:
            revision = await conn.scalar(text("SELECT version_num FROM alembic_version"))
            vector_type = await conn.scalar(
                text(
                    "SELECT format_type(a.atttypid, a.atttypmod) "
                    "FROM pg_attribute a "
                    "JOIN pg_class c ON c.oid = a.attrelid "
                    "WHERE c.relname = 'chunks' AND a.attname = 'embedding' "
                    "AND a.attnum > 0 AND NOT a.attisdropped"
                )
            )
        if revision != REQUIRED_DB_REVISION:
            raise RuntimeError(
                f"database migration mismatch: expected={REQUIRED_DB_REVISION}, actual={revision}"
            )
        expected_vector_type = f"vector({_settings.embedding_dim})"
        if vector_type != expected_vector_type:
            raise RuntimeError(
                f"embedding schema mismatch: expected={expected_vector_type}, actual={vector_type}"
            )
    logger.info("db_initialized")


async def check_db() -> None:
    """执行轻量 ``SELECT 1``；失败时抛出异常供 readiness 返回非 2xx。"""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def close_db() -> None:
    """释放连接池，供滚动退出与测试 teardown 使用；不会关闭外部数据库本身。"""
    await engine.dispose()
    logger.info("db_closed")
