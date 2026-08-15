"""ARQ 告警根因分析 worker：编排 RCA Agent 并推送 IM。

队列生产者通常是受 Bearer Secret 保护的告警 webhook。它把已校验、大小受限的 alert
字典以稳定 job id 入队；本模块从 Redis 消费后运行完整 LangGraph，再调用 IM webhook。
可单独启动::

    arq app.workers.rca_worker.WorkerSettings

也可由 ``app.workers.worker`` 聚合运行。任务超时 300 秒、最多尝试 3 次、结果保留 1 小时。
只有 Agent 和通知都成功才返回 ``"ok"``；通知异常会继续向 ARQ 传播并触发重试。由于
通知是外部副作用，客户端超时后可能重复发送，这是明确的至少一次语义。

worker 启动阶段验证数据库 revision/依赖、安装 LiteLLM 成本回调；关闭阶段先 flush
Langfuse，再关闭 Redis 与数据库连接。生产应给进程足够的 termination grace period，
让这些异步清理完成。
"""

from arq import cron, func

from app.agents.incident_rca.graph import build_rca_agent
from app.core.db import close_db, init_db
from app.core.logging import get_logger, setup_logging
from app.core.observability import close_observability
from app.integrations.notifier import notify
from app.kernel.cost import install_cost_tracking
from app.kernel.queue import close_queue, get_redis_settings
from app.workers.maintenance import cleanup_requirement_plans_task

logger = get_logger("worker.rca")


async def analyze_alert_task(ctx: dict, alert: dict) -> str:
    """对一条告警运行完整根因工作流，并把最终结论推送到 IM。

    Args:
        ctx: ARQ 运行上下文；当前实现不读取它，为后续共享资源预留。
        alert: 已在 API 边界校验过的告警 payload，通常包含 ``alertname`` 和 ``service``。

    Returns:
        Agent 产出且 IM 确认接收后返回 ``"ok"``。

    Agent 的结论可能包含“证据源降级/未验证”的人工复核警告；worker 必须原样推送，不能
    因格式简化而丢失安全提示。任何 LLM、数据源或通知异常都应抛出给 ARQ 重试。
    """
    logger.info("rca_task_start", alertname=alert.get("alertname"))
    agent = build_rca_agent()
    final = await agent.ainvoke({"alert": alert, "iteration": 0})

    conclusion = final.get("conclusion", "（未产出结论）")
    title = f"🔥 根因分析：{alert.get('alertname', '告警')} / {alert.get('service', '')}"
    await notify(f"{title}\n\n{conclusion}")
    logger.info("rca_task_done", alertname=alert.get("alertname"))
    return "ok"


async def _startup(ctx: dict) -> None:
    """在开始取任务前初始化日志、数据库校验和进程级成本追踪。"""
    setup_logging()
    await init_db()
    install_cost_tracking()
    logger.info("rca_worker_startup")


async def _shutdown(ctx: dict) -> None:
    """优雅关闭进程级资源，确保 trace/cost 数据尽可能落盘。"""
    await close_observability()
    await close_queue()
    await close_db()
    logger.info("rca_worker_shutdown")


class WorkerSettings:
    """RCA 专用 ARQ worker 配置。

    ``func`` 的超时必须大于单次 Agent 可能耗时；``max_tries=3`` 处理临时网络故障；
    ``keep_result`` 仅用于短期排障，不替代业务审计。每小时同时执行需求计划保留清理。
    """

    functions = [func(analyze_alert_task, timeout=300, max_tries=3, keep_result=3_600)]
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
