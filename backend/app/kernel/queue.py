"""ARQ/Redis 异步任务队列与跨入口幂等 Job ID。

架构位置：Webhook 和手动 API 只做鉴权、校验与快速入队；ARQ worker 负责拉取 PR、执行
Agent、写回外部系统等耗时任务。所有 API 副本通过同一 Redis 去重并分发任务。

典型调用示例::

    queue = await get_queue()
    job_id = review_job_id("openai", "devmind", 42, head_sha)
    await queue.enqueue_job(
        "review_pr", "openai", "devmind", 42, head_sha, _job_id=job_id
    )

模块维护一个带异步锁的进程级连接池，应用退出时必须 ``await close_queue()``。Job ID 对
业务字段做 SHA-256，既稳定又不在 Redis key 中暴露 payload；相同业务事件的并发入队由
ARQ 去重。这里提供的是“至少一次执行 + 稳定去重键”的基础，不是 exactly-once：任务开始
后崩溃、外部请求超时等仍需 worker 自身的 checkpoint/幂等标记处理，DLQ 也属于后续路线。
"""

import asyncio
import hashlib

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("queue")

_pool: ArqRedis | None = None
_pool_lock = asyncio.Lock()


def make_job_id(kind: str, *parts: object) -> str:
    """为业务事件生成稳定、定长且不暴露原始字段的 ARQ Job ID。

    ``\x1f`` 作为字段分隔符，避免 ``("ab", "c")`` 与 ``("a", "bc")`` 拼接碰撞；摘要
    避免仓库名、提交信息等敏感/超长内容直接进入 Redis key。调用方必须选择能代表一次
    业务事实的全部字段，漏掉版本号会把两个不同事件错误去重。
    """
    canonical = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return f"devmind:{kind}:{digest}"


def review_job_id(owner: str, repo: str, number: int, head_sha: str) -> str:
    """生成 PR 审查任务 ID，使手动入口与 Webhook 落入同一去重域。

    ``head_sha`` 必不可少：同一个 PR 更新后应产生新任务，而同一 commit 的重复 delivery
    应复用 ID。GitHub 回写前仍需再次校验 head SHA，Job ID 本身不能阻止陈旧任务执行。
    """
    return make_job_id("review", owner, repo, number, head_sha)


def get_redis_settings() -> RedisSettings:
    """把已校验的 ``REDIS_URL`` 转成 ARQ 连接配置；不创建网络连接。"""
    return RedisSettings.from_dsn(get_settings().redis_url)


async def get_queue() -> ArqRedis:
    """并发安全地懒创建并返回进程级 ARQ 连接池。

    双重检查避免多个首请求在 ``await create_pool`` 期间建立重复连接；Redis DNS、认证和
    网络错误会向上传播，使入口能够返回明确失败而不是假装任务已经接受。
    """
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                _pool = await create_pool(get_redis_settings())
                logger.info("arq_pool_created")
    return _pool


async def close_queue() -> None:
    """幂等关闭连接池并清空全局引用，供应用 lifespan/测试清理使用。"""
    global _pool
    async with _pool_lock:
        if _pool is not None:
            await _pool.aclose()
            _pool = None
