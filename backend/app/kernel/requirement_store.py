"""需求拆解 HITL 的 PostgreSQL 状态机、租约与 Jira checkpoint。

架构位置：需求 Agent 只生成候选任务；API 调用 ``create_plan`` 持久化等待人工确认的计划，
确认请求调用 ``claim_plan`` 原子获得租约，逐条创建 Jira 后用 ``record_created_issue`` 保存
进度，最终 ``complete_plan``。失败时 ``release_plan`` 允许安全重试，ARQ 定时任务清理过期
记录。

典型调用示例::

    plan = await create_plan(session, thread_id=tid, project="DEV", epic="", tasks=tasks)
    claim = await claim_plan(session, thread_id=tid, approved_tasks=tasks)
    if claim.outcome is ClaimOutcome.CLAIMED:
        await record_created_issue(session, thread_id=tid,
                                   lease_token=claim.lease_token,
                                   task_index=0, issue={"key": "DEV-1"})
        await complete_plan(session, thread_id=tid,
                            lease_token=claim.lease_token, result=result)

为什么不用进程内 MemorySaver：人工确认可能跨小时、重启和 API 副本；数据库 CAS 才能保证
同一时刻只有一个副本执行 Jira 副作用。任务规范化 JSON 的 SHA-256 指纹防止同一计划被
两个不同批准版本认领，随机 lease token 防止旧执行者覆盖新执行者。每个函数自行提交或
回滚，形成明确持久化边界；但 PostgreSQL 与 Jira 之间仍非分布式事务，外部超时需依赖 Jira
幂等标签和人工核对。当前保留 24 小时、一小时租约；审批链、多级 RBAC 和 outbox 属未来路线。
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.requirement import RequirementPlan

PLAN_TTL = timedelta(hours=24)
# 最多 100 个 Jira 任务、每次外部调用 30 秒；一小时租约可避免正常慢请求被误抢占，
# 同时允许进程崩溃后的计划最终恢复。
PROCESSING_LEASE = timedelta(hours=1)


class ClaimOutcome(StrEnum):
    """计划认领的完备业务结果，供 API 映射为幂等响应或冲突状态。"""

    CLAIMED = "claimed"
    COMPLETED = "completed"
    PROCESSING = "processing"
    EXPIRED = "expired"
    CONFLICT = "conflict"
    MISSING = "missing"


@dataclass(frozen=True)
class PlanClaim:
    """认领结果；只有 ``CLAIMED`` 必须携带新的 ``lease_token``。

    ``COMPLETED`` 等结果可携带现有 plan 供 API 返回先前结果；``MISSING`` 通常没有 plan。
    """

    outcome: ClaimOutcome
    plan: RequirementPlan | None = None
    lease_token: str | None = None


async def create_plan(
    session: AsyncSession,
    *,
    thread_id: str,
    project: str,
    epic: str,
    tasks: list[dict],
) -> RequirementPlan:
    """持久化 24 小时有效的待确认计划，并完整提交本次事务。

    ``thread_id`` 应由入口生成且数据库唯一；IntegrityError 等会 rollback 后向上传播。任务
    在进入本函数前应已通过 API schema 数量/字段校验。
    """
    now = datetime.now(UTC)
    plan = RequirementPlan(
        thread_id=thread_id,
        project=project,
        epic=epic,
        tasks=tasks,
        expires_at=now + PLAN_TTL,
    )
    session.add(plan)
    try:
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return plan


async def claim_plan(
    session: AsyncSession,
    *,
    thread_id: str,
    approved_tasks: list[dict],
) -> PlanClaim:
    """用单条条件 UPDATE 原子认领计划，并区分未命中的业务原因。

    可认领 pending，或更新时间早于一小时的失联 processing 计划；必须未过期且批准任务
    指纹与历史一致。UPDATE 成功时同时写 processing、任务、指纹和随机租约。失败后的 SELECT
    只用于解释结果，不参与副作用互斥；真正互斥由前面的数据库原子 UPDATE 保证。
    """
    now = datetime.now(UTC)
    stale_before = now - PROCESSING_LEASE
    lease_token = uuid.uuid4().hex
    approved_fingerprint = hashlib.sha256(
        json.dumps(
            approved_tasks,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    claimable = or_(
        RequirementPlan.status == "pending",
        and_(
            RequirementPlan.status == "processing",
            RequirementPlan.updated_at < stale_before,
        ),
    )
    stmt = (
        update(RequirementPlan)
        .where(
            RequirementPlan.thread_id == thread_id,
            RequirementPlan.expires_at > now,
            claimable,
            or_(
                RequirementPlan.approved_fingerprint.is_(None),
                RequirementPlan.approved_fingerprint == approved_fingerprint,
            ),
        )
        .values(
            status="processing",
            tasks=approved_tasks,
            approved_fingerprint=approved_fingerprint,
            lease_token=lease_token,
            updated_at=now,
        )
        .returning(RequirementPlan)
    )
    try:
        claimed = (await session.execute(stmt)).scalar_one_or_none()
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    if claimed is not None:
        return PlanClaim(ClaimOutcome.CLAIMED, claimed, lease_token)

    # UPDATE 未命中时区分不存在、过期、已完成和正在被其它副本处理。
    plan = await session.scalar(
        select(RequirementPlan).where(RequirementPlan.thread_id == thread_id)
    )
    if plan is None:
        return PlanClaim(ClaimOutcome.MISSING)
    if plan.status == "completed":
        return PlanClaim(ClaimOutcome.COMPLETED, plan)
    if plan.expires_at <= now:
        return PlanClaim(ClaimOutcome.EXPIRED, plan)
    if plan.status == "pending" and plan.approved_fingerprint != approved_fingerprint:
        return PlanClaim(ClaimOutcome.CONFLICT, plan)
    return PlanClaim(ClaimOutcome.PROCESSING, plan)


async def complete_plan(
    session: AsyncSession,
    *,
    thread_id: str,
    lease_token: str,
    result: dict,
) -> bool:
    """仅当前 processing 租约持有者可提交最终结果。

    返回 ``False`` 表示租约已丢失/计划状态改变，调用方不得把本次结果宣称为成功。结果和
    清空 token 在同一次 UPDATE 提交。
    """
    stmt = (
        update(RequirementPlan)
        .where(
            RequirementPlan.thread_id == thread_id,
            RequirementPlan.status == "processing",
            RequirementPlan.lease_token == lease_token,
        )
        .values(
            status="completed",
            result=result,
            lease_token=None,
            updated_at=datetime.now(UTC),
        )
    )
    try:
        changed = (await session.execute(stmt)).rowcount == 1
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return changed


async def record_created_issue(
    session: AsyncSession,
    *,
    thread_id: str,
    lease_token: str,
    task_index: int,
    issue: dict,
) -> None:
    """在有效租约下按任务下标 upsert Jira checkpoint 并立即提交。

    保存下标而非标题是因为标题可重复/编辑。先读取当前进度再带租约条件 UPDATE；若期间
    租约变化，rowcount 为零并失败，旧执行者不能覆盖新执行者。每条外部创建后立即提交，
    让批次中途失败的重试跳过已知成功项。
    """
    plan = await session.scalar(
        select(RequirementPlan).where(
            RequirementPlan.thread_id == thread_id,
            RequirementPlan.status == "processing",
            RequirementPlan.lease_token == lease_token,
        )
    )
    if plan is None:
        await session.rollback()
        raise RuntimeError("requirement plan lease lost")

    progress = [dict(item) for item in (plan.progress or [])]
    entry = {"task_index": task_index, **issue}
    replaced = False
    for index, item in enumerate(progress):
        if item.get("task_index") == task_index:
            progress[index] = entry
            replaced = True
            break
    if not replaced:
        progress.append(entry)
    progress.sort(key=lambda item: int(item.get("task_index", 0)))

    stmt = (
        update(RequirementPlan)
        .where(
            RequirementPlan.thread_id == thread_id,
            RequirementPlan.status == "processing",
            RequirementPlan.lease_token == lease_token,
        )
        .values(progress=progress, updated_at=datetime.now(UTC))
    )
    try:
        changed = (await session.execute(stmt)).rowcount == 1
        if not changed:
            raise RuntimeError("requirement plan lease lost")
        await session.commit()
    except BaseException:
        await session.rollback()
        raise


async def release_plan(session: AsyncSession, *, thread_id: str, lease_token: str) -> None:
    """外部写入失败时把当前租约恢复为 pending，保留任务和 checkpoint。

    UPDATE 带 token 条件，旧执行者释放不了新租约。若 Jira 请求结果未知，调用方应先依赖
    幂等标签查询；直接重试虽会复用 checkpoint，但最后一次未落 checkpoint 的 Issue 仍可能
    已在外部创建。
    """
    stmt = (
        update(RequirementPlan)
        .where(
            RequirementPlan.thread_id == thread_id,
            RequirementPlan.status == "processing",
            RequirementPlan.lease_token == lease_token,
        )
        .values(status="pending", lease_token=None, updated_at=datetime.now(UTC))
    )
    try:
        await session.execute(stmt)
        await session.commit()
    except BaseException:
        await session.rollback()
        raise


async def cleanup_expired_plans(session: AsyncSession, *, now: datetime | None = None) -> int:
    """删除过期计划，同时避免与仍有效的 Jira 确认租约竞争。

    pending/completed 到 TTL 即可删除；processing 必须同时超过 TTL 且租约一小时未更新才
    删除，避免长批次执行期间 checkpoint 被清走。``now`` 仅用于确定性测试；生产省略并使用
    UTC 当前时间。返回实际删除行数，事务失败会 rollback 并向上传播。
    """
    current = now or datetime.now(UTC)
    stale_before = current - PROCESSING_LEASE
    stmt = delete(RequirementPlan).where(
        RequirementPlan.expires_at < current,
        or_(
            RequirementPlan.status != "processing",
            and_(
                RequirementPlan.status == "processing",
                RequirementPlan.updated_at < stale_before,
            ),
        ),
    )
    try:
        result = await session.execute(stmt)
        deleted = result.rowcount or 0
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return deleted
