"""需求拆解 API：以 PostgreSQL 持久化人工确认（HITL）的两阶段工作流。

1. ``POST /api/requirement/plan``：读 PRD/Confluence → LLM 拆任务 → 保存 pending 计划；
2. ``POST /api/requirement/confirm``：批准完整快照 → 原子认领 → 逐条写 Jira/checkpoint → 完成。

plan 示例为 ``{"prd_text":"新增审批流","project":"DEV"}``；把响应中的 ``thread_id`` 和可编辑
``tasks`` 原样/修改后传给 confirm。人工闸门保证 Jira 副作用永远发生在 confirm 之后。

生产边界：PostgreSQL 与 Jira 无法组成原子事务，本模块以 CAS 租约、任务指纹、稳定外部幂等
标识和逐项 progress 缩小重复窗口；它是“可恢复、尽力幂等”而非数学上的 exactly-once。
计划有 TTL，多副本可共享状态；调用方超时后应使用相同内容重试，确认开始后不能更改任务。
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.requirement.graph import build_requirement_agent, write_jira_tasks
from app.core.db import get_session
from app.core.logging import get_logger
from app.integrations.confluence import (
    ConfluenceClient,
    ConfluencePageIdError,
    ConfluenceResponseTooLarge,
)
from app.integrations.jira import JiraConfigurationError
from app.kernel.requirement_store import (
    ClaimOutcome,
    claim_plan,
    complete_plan,
    create_plan,
    record_created_issue,
    release_plan,
)
from app.schemas.requirement import (
    MAX_REQUIREMENT_TASKS,
    ConfirmRequest,
    PlanRequest,
    PlanResponse,
    TaskItem,
)

logger = get_logger("gateway.requirement")
router = APIRouter(prefix="/requirement", tags=["requirement"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/plan", response_model=PlanResponse)
async def plan(req: PlanRequest, session: SessionDep) -> PlanResponse:
    """读取并拆解 PRD，持久化提议任务后暂停；本阶段绝不写 Jira。"""
    prd = req.prd_text.strip()
    if not prd and req.confluence_url.strip():
        confluence = ConfluenceClient()
        if not confluence.configured:
            raise HTTPException(status_code=503, detail="Confluence 集成不可用")
        try:
            prd = await confluence.get_page_text(req.confluence_url.strip())
        except ConfluencePageIdError:
            raise HTTPException(status_code=400, detail="Confluence 页面标识无效") from None
        except ConfluenceResponseTooLarge:
            raise HTTPException(status_code=502, detail="Confluence 页面内容超过处理上限") from None
        except Exception as exc:  # noqa: BLE001
            logger.exception("confluence_load_failed")
            raise HTTPException(status_code=502, detail="读取 Confluence 失败") from exc

    agent = build_requirement_agent()
    thread_id = uuid.uuid4().hex
    try:
        result = await agent.ainvoke({"prd": prd})
    except Exception as exc:  # noqa: BLE001
        logger.exception("req_propose_failed")
        raise HTTPException(status_code=502, detail="需求拆解服务暂时不可用") from exc

    # 安全代理会统一收紧 TaskItem 字段边界；这里用响应模型提前过滤 LLM 的畸形输出，
    # 防止无效状态写入持久层。
    raw_tasks = result.get("tasks") or []
    if not isinstance(raw_tasks, list):
        raw_tasks = []
    if len(raw_tasks) > MAX_REQUIREMENT_TASKS:
        logger.warning(
            "req_tasks_truncated",
            generated=len(raw_tasks),
            retained=MAX_REQUIREMENT_TASKS,
        )
    tasks = []
    for item in raw_tasks[:MAX_REQUIREMENT_TASKS]:
        try:
            tasks.append(TaskItem.model_validate(item))
        except (TypeError, ValueError):
            logger.warning("req_task_discarded", item_type=type(item).__name__)
    if not tasks:
        raise HTTPException(status_code=502, detail="模型未生成有效任务，请重试")

    serialized = [task.model_dump() for task in tasks]
    try:
        await create_plan(
            session,
            thread_id=thread_id,
            project=req.project,
            epic=req.epic,
            tasks=serialized,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("req_plan_persist_failed")
        raise HTTPException(status_code=503, detail="计划暂时无法保存，请稍后重试") from exc
    logger.info("req_plan_done", thread_id=thread_id, tasks=len(tasks))
    return PlanResponse(thread_id=thread_id, tasks=tasks)


@router.post("/confirm")
async def confirm(req: ConfirmRequest, session: SessionDep) -> dict:
    """人工确认后认领计划、恢复工作流并将任务写入 Jira。

    状态码约定：404 不存在、410 已过期、409 正被处理/内容冲突/租约丢失、502 外部 Jira
    失败、503 本地状态或配置不可用；completed 计划直接返回首次结果以支持客户端安全重试。
    """
    approved = [t.model_dump() for t in req.tasks]
    try:
        claim = await claim_plan(session, thread_id=req.thread_id, approved_tasks=approved)
    except Exception as exc:  # noqa: BLE001
        logger.exception("req_claim_failed")
        raise HTTPException(status_code=503, detail="计划状态暂时不可用") from exc

    if claim.outcome == ClaimOutcome.MISSING:
        raise HTTPException(status_code=404, detail="计划不存在")
    if claim.outcome == ClaimOutcome.EXPIRED:
        raise HTTPException(status_code=410, detail="计划已过期，请重新拆解")
    if claim.outcome == ClaimOutcome.PROCESSING:
        raise HTTPException(status_code=409, detail="计划正在确认中，请勿重复提交")
    if claim.outcome == ClaimOutcome.CONFLICT:
        raise HTTPException(status_code=409, detail="计划已开始写入，重试时任务内容不可变更")
    if claim.outcome == ClaimOutcome.COMPLETED:
        # confirm 天然幂等：客户端超时重试时返回第一次写入的持久化结果。
        return claim.plan.result or {}

    # claim_plan 已在短数据库事务中完成 compare-and-set。assert 表达内部不变量，而不是用来
    # 校验用户输入；若 outcome=CLAIMED 却无 plan/token，说明 store 实现存在程序错误。
    assert claim.plan is not None and claim.lease_token is not None

    async def _save_progress(task_index: int, issue: dict) -> None:
        """每创建一个 Jira issue 立即 checkpoint，缩小进程崩溃后的重复副作用窗口。"""
        await record_created_issue(
            session,
            thread_id=req.thread_id,
            lease_token=claim.lease_token,
            task_index=task_index,
            issue=issue,
        )

    # 外部 Jira 请求期间不持数据库锁：长事务会耗尽连接池并阻塞其它副本。lease token 作为
    # 所有后续短事务的所有权证明，失败时 release 允许相同任务快照恢复。
    try:
        result = await write_jira_tasks(
            approved,
            project=claim.plan.project,
            epic=claim.plan.epic,
            idempotency_prefix=f"devmind-{req.thread_id}",
            progress=claim.plan.progress,
            on_created=_save_progress,
        )
    except JiraConfigurationError as exc:
        logger.error("req_jira_configuration_invalid", thread_id=req.thread_id)
        try:
            await release_plan(
                session,
                thread_id=req.thread_id,
                lease_token=claim.lease_token,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "req_lease_release_failed",
                thread_id=req.thread_id,
            )
        raise HTTPException(status_code=503, detail="Jira 配置不完整") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("req_jira_write_failed", thread_id=req.thread_id)
        try:
            await release_plan(
                session,
                thread_id=req.thread_id,
                lease_token=claim.lease_token,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "req_lease_release_failed",
                thread_id=req.thread_id,
            )
        raise HTTPException(status_code=502, detail="写入 Jira 失败，可稍后重试") from exc

    try:
        completed = await complete_plan(
            session,
            thread_id=req.thread_id,
            lease_token=claim.lease_token,
            result=result,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("req_complete_failed", thread_id=req.thread_id)
        # 每个 Jira key 已逐条落在 progress；释放租约后，重试只会组装已有结果并
        # 再次完成状态，不会重放外部副作用。
        try:
            await release_plan(
                session,
                thread_id=req.thread_id,
                lease_token=claim.lease_token,
            )
        except Exception:  # noqa: BLE001
            logger.exception("req_complete_release_failed", thread_id=req.thread_id)
        raise HTTPException(status_code=503, detail="Jira 已写入，但结果状态保存失败") from exc
    if not completed:
        logger.error("req_lease_lost", thread_id=req.thread_id)
        raise HTTPException(status_code=409, detail="计划确认租约已失效")
    return result
