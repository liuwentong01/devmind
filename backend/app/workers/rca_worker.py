"""ARQ worker：异步执行告警根因分析并推送 IM。

启动：
    arq app.workers.rca_worker.WorkerSettings
"""

from app.agents.incident_rca.graph import build_rca_agent
from app.core.logging import get_logger, setup_logging
from app.integrations.notifier import notify
from app.kernel.queue import get_redis_settings

logger = get_logger("worker.rca")


async def analyze_alert_task(ctx: dict, alert: dict) -> str:
    """对一条告警跑完整根因工作流，并把结论推送到 IM。"""
    logger.info("rca_task_start", alertname=alert.get("alertname"))
    agent = build_rca_agent()
    final = await agent.ainvoke({"alert": alert, "iteration": 0})

    conclusion = final.get("conclusion", "（未产出结论）")
    title = f"🔥 根因分析：{alert.get('alertname', '告警')} / {alert.get('service', '')}"
    await notify(f"{title}\n\n{conclusion}")
    logger.info("rca_task_done", alertname=alert.get("alertname"))
    return "ok"


async def _startup(ctx: dict) -> None:
    setup_logging()
    logger.info("rca_worker_startup")


class WorkerSettings:
    """ARQ worker 配置。"""

    functions = [analyze_alert_task]
    on_startup = _startup
    redis_settings = get_redis_settings()
