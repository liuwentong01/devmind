"""统一 ARQ worker：在一个进程注册平台全部异步任务。

默认生产入口::

    arq app.workers.worker.WorkerSettings

它同时消费 PR Review 和 RCA 告警任务，并每小时执行一次需求计划清理。小规模部署使用统一
入口最简单；当 Review 与 RCA 的吞吐、超时或资源需求差异明显时，可改为分别部署
``review_worker`` 与 ``rca_worker``。同一任务不要被统一和专用 worker 使用不同队列规则
重复投递；ARQ 多消费者本身会保证一个 job 只由一个 worker 领取，但副作用仍按至少一次
语义设计。

启动函数在开始消费前完成日志、数据库/迁移状态和成本追踪初始化；任一步失败都会阻止
worker 接单（fail-closed）。关闭函数 flush Langfuse 并释放 Redis/数据库连接。Kubernetes
应配置优雅终止窗口，而不是直接 SIGKILL。
"""

from arq import cron, func

from app.core.db import close_db, init_db
from app.core.logging import get_logger, setup_logging
from app.core.observability import close_observability
from app.kernel.cost import install_cost_tracking
from app.kernel.queue import close_queue, get_redis_settings
from app.workers.maintenance import cleanup_requirement_plans_task
from app.workers.rca_worker import analyze_alert_task
from app.workers.review_worker import review_pull_task

logger = get_logger("worker")


async def _startup(ctx: dict) -> None:
    """初始化所有任务共用的进程资源；失败时不开始消费。"""
    setup_logging()
    await init_db()
    install_cost_tracking()
    logger.info("worker_startup", functions=["review_pull_task", "analyze_alert_task"])


async def _shutdown(ctx: dict) -> None:
    """优雅刷新遥测并关闭共享 Redis、数据库资源。"""
    await close_observability()
    await close_queue()
    await close_db()
    logger.info("worker_shutdown")


class WorkerSettings:
    """聚合所有任务的 ARQ 声明式配置。

    ``functions`` 定义每类任务的超时、最大尝试次数和结果保留期；任务函数抛异常才会触发
    重试。``cron_jobs`` 在每小时 0 分执行保留清理。``redis_settings`` 来自统一 Settings，
    CLI 导入本模块后由 ARQ 使用它建立队列连接。
    """

    functions = [
        func(review_pull_task, timeout=180, max_tries=3, keep_result=86_400),
        func(analyze_alert_task, timeout=300, max_tries=3, keep_result=3_600),
    ]
    cron_jobs = [
        cron(
            cleanup_requirement_plans_task,
            minute=0,
            timeout=60,
            max_tries=3,
        )
    ]
    on_startup = _startup
    on_shutdown = _shutdown
    redis_settings = get_redis_settings()
