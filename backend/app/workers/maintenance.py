"""所有 ARQ worker 共享的周期性数据保留任务。

需求拆解的 Human-in-the-loop 计划保存在 PostgreSQL 中，若用户一直不确认会形成无界历史
状态。本模块提供按 TTL 清理的 cron 入口；存储层会保留尚未过期的计划以及仍持有有效
处理租约的计划，避免定时任务删除正在批量写 Jira 的工作。

``WorkerSettings`` 每小时整点注册一次该函数，也可以在调试环境直接调用::

    deleted = await cleanup_requirement_plans_task({})

ARQ 会传入 ``ctx``，当前任务不需要其中的 Redis/自定义资源，所以参数被保留但不读取。
异常不得在这里吞掉：数据库故障应让 ARQ 按 ``max_tries`` 重试并暴露监控信号。
"""

from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.kernel.requirement_store import cleanup_expired_plans

logger = get_logger("worker.maintenance")


async def cleanup_requirement_plans_task(ctx: dict) -> int:
    """删除已过期的需求 HITL 状态，同时保留仍有效的处理租约。

    每次执行创建独立 SQLAlchemy session；具体 TTL、租约判断和事务提交由
    :func:`cleanup_expired_plans` 负责。返回删除行数，便于 ARQ 结果检查与结构化日志统计。
    """
    async with SessionLocal() as session:
        deleted = await cleanup_expired_plans(session)
    logger.info("requirement_plans_cleaned", deleted=deleted)
    return deleted
