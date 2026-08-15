"""ARQ PR 审查 worker：拉取 diff、检索规范、调用 LLM 并回写 GitHub。

API 手动触发和 GitHub webhook 会共用同一种稳定 review job id，使同一
``owner/repo/PR/head SHA`` 不会被两个入口重复入队。可单独启动::

    arq app.workers.review_worker.WorkerSettings

任务按以下顺序执行：读取当前 head SHA → 拒绝过期任务 → 获取有界 diff → 只检索配置的
Review 规则数据源 → 调用 LLM → 再次读取 head SHA → 按隐藏 marker 幂等回写。前后两次
SHA 检查很重要：模型调用可能持续数十秒，期间开发者可能 push 新提交。

任务超时 180 秒、最多尝试 3 次、结果保留 24 小时。GitHub 传输失败会让 ARQ 重试；
marker 搜索降低“上游已创建但响应丢失”造成的重复评论风险。启动/关闭资源管理与聚合
worker 保持一致。
"""

from arq import cron, func

from app.agents.code_review.reviewer import build_review_messages, load_review_rules
from app.core.db import SessionLocal, close_db, init_db
from app.core.logging import get_logger, setup_logging
from app.core.observability import close_observability
from app.integrations.github import GitHubClient
from app.kernel.cost import install_cost_tracking
from app.kernel.llm.gateway import get_chat_model
from app.kernel.queue import close_queue, get_redis_settings
from app.workers.maintenance import cleanup_requirement_plans_task

logger = get_logger("worker.review")

REVIEW_FOOTER = "\n\n---\n_🤖 由 DevMind 代码审查 Agent 自动生成，仅供参考。_"


async def review_pull_task(
    ctx: dict,
    owner: str,
    repo: str,
    number: int,
    expected_head_sha: str | None = None,
) -> str:
    """拉取 PR diff → 检索团队规范 → LLM 审查 → 幂等回写 Review。

    Args:
        ctx: ARQ 上下文；当前任务不读取，保留用于未来依赖注入。
        owner: GitHub 仓库 owner/organization。
        repo: 仓库短名。
        number: PR 数字编号。
        expected_head_sha: 入队时观察到的 head SHA；传入时用于立即丢弃陈旧任务。

    Returns:
        ``"ok"`` 表示流程完成（包括既有 marker 被复用），``"stale"`` 表示 PR 已更新，
        ``"empty diff"`` 表示没有可审查内容。这些是正常终态，不应触发 ARQ 重试。

    LLM/数据库/GitHub 异常不在此捕获，以便 ARQ 根据 ``max_tries`` 重试。review 正文追加
    免责声明和仅机器可见的 SHA marker，既提示人工判断，又为跨重试去重提供依据。
    """
    logger.info("review_task_start", owner=owner, repo=repo, number=number)
    gh = GitHubClient()

    head_sha = await gh.get_pull_head_sha(owner, repo, number)
    if expected_head_sha and head_sha != expected_head_sha:
        logger.info(
            "review_task_stale",
            owner=owner,
            repo=repo,
            number=number,
            expected=expected_head_sha,
            actual=head_sha,
        )
        return "stale"

    diff = await gh.get_pull_diff(owner, repo, number)
    if not diff.strip():
        logger.info("review_task_empty_diff", owner=owner, repo=repo, number=number)
        return "empty diff"

    async with SessionLocal() as session:
        rules = await load_review_rules(session, diff)

    messages = build_review_messages(diff, rules)
    model = get_chat_model()
    resp = await model.ainvoke(messages)

    # LLM 调用可能持续数十秒。期间若 PR 又 push 了新 commit，不能把旧 diff 的结论
    # 回写到新 head；新的 synchronize webhook 会产生另一个具有新 SHA/job id 的任务。
    latest_head_sha = await gh.get_pull_head_sha(owner, repo, number)
    if latest_head_sha != head_sha:
        logger.info(
            "review_task_stale_before_post",
            owner=owner,
            repo=repo,
            number=number,
            reviewed=head_sha,
            actual=latest_head_sha,
        )
        return "stale"

    marker = f"<!-- devmind-review:{head_sha} -->"
    review_body = str(resp.content) + REVIEW_FOOTER + f"\n{marker}"

    await gh.post_review_once(owner, repo, number, review_body, marker=marker)
    logger.info("review_task_done", owner=owner, repo=repo, number=number)
    return "ok"


async def _startup(ctx: dict) -> None:
    """消费队列前初始化日志、校验数据库并安装一次成本回调。"""
    setup_logging()
    await init_db()
    install_cost_tracking()
    logger.info("review_worker_startup")


async def _shutdown(ctx: dict) -> None:
    """按可观测性 → Redis → 数据库的顺序释放进程资源。"""
    await close_observability()
    await close_queue()
    await close_db()
    logger.info("review_worker_shutdown")


class WorkerSettings:
    """代码审查专用 ARQ worker 配置。

    审查最多运行 180 秒并重试 3 次；保留一天结果便于排查 webhook 与手动触发的状态。
    cron 清理任务和生命周期钩子与聚合 worker 相同。
    """

    functions = [func(review_pull_task, timeout=180, max_tries=3, keep_result=86_400)]
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
