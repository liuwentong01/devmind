"""异步任务队列：基于 ARQ + Redis（async 原生）。

事件驱动入口（Webhook）只负责快速入队，耗时的 Agent 任务交给 worker 异步执行，
避免阻塞请求、支持重试与横向扩展。
"""

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("queue")

_pool: ArqRedis | None = None


def get_redis_settings() -> RedisSettings:
    """从 REDIS_URL 解析出 ARQ 的 Redis 连接配置。"""
    return RedisSettings.from_dsn(get_settings().redis_url)


async def get_queue() -> ArqRedis:
    """获取（懒加载）ARQ 连接池，用于入队任务。"""
    global _pool
    if _pool is None:
        _pool = await create_pool(get_redis_settings())
        logger.info("arq_pool_created")
    return _pool


async def close_queue() -> None:
    """关闭连接池（应用退出时调用）。"""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
