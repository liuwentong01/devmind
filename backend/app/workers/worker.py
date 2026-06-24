"""统一 ARQ worker：注册平台所有异步任务。

一个 worker 进程处理全部队列任务，启动：
    arq app.workers.worker.WorkerSettings
"""

from app.core.logging import get_logger, setup_logging
from app.kernel.queue import get_redis_settings
from app.workers.rca_worker import analyze_alert_task
from app.workers.review_worker import review_pull_task

logger = get_logger("worker")


async def _startup(ctx: dict) -> None:
    setup_logging()
    logger.info("worker_startup", functions=["review_pull_task", "analyze_alert_task"])


class WorkerSettings:
    """ARQ worker 配置（聚合所有任务）。"""

    functions = [review_pull_task, analyze_alert_task]
    on_startup = _startup
    redis_settings = get_redis_settings()
