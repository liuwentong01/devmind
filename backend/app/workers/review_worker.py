"""ARQ worker：异步执行 PR 代码审查并回写 GitHub。

启动：
    arq app.workers.review_worker.WorkerSettings
"""

from app.agents.code_review.reviewer import build_review_messages, load_review_rules
from app.core.db import SessionLocal
from app.core.logging import get_logger, setup_logging
from app.integrations.github import GitHubClient
from app.kernel.llm.gateway import get_chat_model
from app.kernel.queue import get_redis_settings

logger = get_logger("worker.review")

REVIEW_FOOTER = "\n\n---\n_🤖 由 DevMind 代码审查 Agent 自动生成，仅供参考。_"


async def review_pull_task(ctx: dict, owner: str, repo: str, number: int) -> str:
    """拉取 PR diff → 检索团队规范 → LLM 审查 → 回写 PR Review。"""
    logger.info("review_task_start", owner=owner, repo=repo, number=number)
    gh = GitHubClient()

    diff = await gh.get_pull_diff(owner, repo, number)
    if not diff.strip():
        logger.info("review_task_empty_diff", owner=owner, repo=repo, number=number)
        return "empty diff"

    async with SessionLocal() as session:
        rules = await load_review_rules(session, diff)

    messages = build_review_messages(diff, rules)
    model = get_chat_model()
    resp = await model.ainvoke(messages)
    review_body = str(resp.content) + REVIEW_FOOTER

    await gh.post_review(owner, repo, number, review_body)
    logger.info("review_task_done", owner=owner, repo=repo, number=number)
    return "ok"


async def _startup(ctx: dict) -> None:
    setup_logging()
    logger.info("review_worker_startup")


class WorkerSettings:
    """ARQ worker 配置。"""

    functions = [review_pull_task]
    on_startup = _startup
    redis_settings = get_redis_settings()
