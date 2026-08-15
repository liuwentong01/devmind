"""Alembic 的异步 PostgreSQL 迁移运行环境。

调用链：在 ``backend`` 目录执行 ``alembic upgrade head`` 后，Alembic 加载本文件、导入
所有 ORM metadata，并选择 ``MIGRATION_DATABASE_URL``（优先）或 ``DATABASE_URL``。
``--sql`` 使用离线模式生成 SQL；普通执行使用 psycopg 异步连接在线迁移。

生产边界：迁移进程可使用独立 DDL 账号，应用进程只应持 DML 账号。这里使用 ``NullPool``
避免一次性 Job 留下连接池；模型导入只用于 autogenerate 对比，生产发布仍必须审阅迁移 SQL、
备份并在应用接流量前完成 upgrade，不能靠 ``Base.metadata.create_all`` 演进表结构。
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import get_settings
from app.core.db import Base
from app.models import knowledge, requirement  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
migration_url = settings.migration_database_url or settings.database_url
config.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """不连接数据库生成迁移 SQL，适合发布审阅：``alembic upgrade head --sql``。"""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_sync_migrations(connection) -> None:  # noqa: ANN001
    """在异步连接提供的同步桥上配置上下文并执行 revision。"""
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """创建一次性异步连接并执行真实 DDL，结束后显式释放驱动资源。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_run_sync_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
