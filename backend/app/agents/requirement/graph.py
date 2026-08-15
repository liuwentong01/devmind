"""需求拆解 Agent 与确认后 Jira 写入编排。

架构位置：LangGraph 仅执行无外部业务副作用的 ``propose``，把 PRD 转成候选任务。API 将
候选计划写入 :mod:`app.kernel.requirement_store`，人工确认时通过数据库 compare-and-set
租约认领计划，再调用本模块 ``write_jira_tasks``。计划和确认因此可以落到不同 API 副本，
也不依赖进程内 ``MemorySaver``。

典型两阶段调用::

    proposed = await build_requirement_agent().ainvoke({"prd": prd_text})
    plan = await create_plan(session, thread_id=tid, tasks=proposed["tasks"], ...)
    claim = await claim_plan(session, thread_id=tid, approved_tasks=approved)
    if claim.outcome is ClaimOutcome.CLAIMED:
        result = await write_jira_tasks(
            approved,
            project=plan.project,
            epic=plan.epic,
            idempotency_prefix=tid,
            progress=plan.progress,
            on_created=save_checkpoint,
        )

副作用与失败边界：提议会调用 LLM；写入函数会逐条访问 Jira，并可在每条成功后执行持久化
回调。调用方必须持有有效租约、在失败时释放租约、在全部成功后完成计划。标签幂等和进度
checkpoint 降低重复 Issue 风险，但网络超时可能处于“Jira 已创建、客户端未知”状态，仍需
通过标签查询/人工核对。当前能力不自动规划任务依赖、Sprint 或复杂 Jira 工作流。
"""

from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app.agents.incident_rca.analyzer import parse_json
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.prompt_security import UNTRUSTED_CONTEXT_POLICY
from app.integrations.jira import (
    ISSUETYPE_MAP,
    JiraClient,
    JiraConfigurationError,
)
from app.kernel.llm.gateway import get_chat_model

logger = get_logger("agent.requirement")

PROPOSE_SYSTEM = (
    UNTRUSTED_CONTEXT_POLICY + "\n\n你是研发项目经理。把 PRD 拆解为可执行的前后端开发任务。\n"
    "只输出 JSON 数组，每个元素形如："
    '{"title": "任务标题", "type": "backend|frontend", '
    '"description": "任务说明", "story_points": 1|2|3|5|8, '
    '"acceptance_criteria": "验收标准"}。\n'
    "拆解要点：按功能模块切分，前后端分离，粒度适中（单任务 ≤ 8 点）。不要输出 JSON 以外内容。"
)


class ReqState(TypedDict, total=False):
    """需求提议图状态。

    当前节点实际只消费 ``prd`` 并产出 ``tasks``；``project``、``epic`` 保留给 API 的确认
    语义，不由模型擅自决定。``tasks`` 仍是未经信任的模型输出，上层 schema 会再次验证并
    限制最多 100 条。
    """

    prd: str
    project: str
    epic: str
    tasks: list[dict]  # LLM 提议


async def _propose(state: ReqState) -> ReqState:
    """让 LLM 把 PRD 拆成 JSON 任务列表，仅更新 ``tasks``。

    模型返回无法解析或顶层不是数组时安全降级为空列表。这里不创建 Jira Issue，也不宣称
    任务已经获得用户批准；“提议”和“执行”之间必须保留持久化人工闸门。
    """
    model = get_chat_model()
    resp = await model.ainvoke(
        [SystemMessage(PROPOSE_SYSTEM), HumanMessage(f"【PRD】\n{state['prd']}")]
    )
    parsed = parse_json(str(resp.content))
    tasks = parsed if isinstance(parsed, list) else []
    logger.info("req_proposed", count=len(tasks))
    return {"tasks": tasks}


async def write_jira_tasks(
    tasks: list[dict],
    *,
    project: str = "",
    epic: str = "",
    idempotency_prefix: str,
    progress: list[dict] | None = None,
    on_created: Callable[[int, dict], Awaitable[None]] | None = None,
) -> dict:
    """把已确认任务逐条幂等写入 Jira，并在每条成功后保存 checkpoint。

    Args:
        tasks: 已经由 API schema 和人工确认的任务列表。
        project: Jira Project Key；为空时使用配置默认值。
        epic: 可选父 Epic Key。
        idempotency_prefix: 稳定的计划标识，和任务下标共同形成 Jira 标签。
        progress: 之前已经成功的 checkpoint，重试时按 ``task_index`` 跳过。
        on_created: 每个 Issue 成功后调用的异步持久化函数；应验证当前数据库租约。

    Returns:
        ``created``（按输入顺序）、``dry_run``、``project`` 与 ``epic``。dry-run 只允许在
        配置明确许可的本地环境出现。

    Raises:
        JiraConfigurationError: 非演示环境未配置 Project 或 Jira 凭据。
        httpx.HTTPError: Jira 请求失败。
        RuntimeError: checkpoint 回调发现租约丢失等一致性错误。

    为什么在每条成功后回调：批量中第 N 条失败时，前 N-1 条已是不可回滚的外部副作用；
    持久化进度能让下一次租约重试跳过它们。回调在 ``created.append`` 前执行，确保只有进度
    已落库的 Issue 才会被本次结果视为成功。
    """
    client = JiraClient()
    project = (project or get_settings().jira_default_project).strip()
    if not project:
        if not client.configured and client.dry_run_allowed:
            project = "DEV"
        else:
            raise JiraConfigurationError(
                "A Jira project or JIRA_DEFAULT_PROJECT must be configured"
            )
    epic_key = epic or None

    # checkpoint 以输入下标为稳定主键，而不是用可编辑/可重复的任务标题匹配。
    saved_by_index = {
        int(item["task_index"]): item
        for item in (progress or [])
        if isinstance(item, dict) and isinstance(item.get("task_index"), int)
    }
    created = []
    for index, task in enumerate(tasks):
        saved = saved_by_index.get(index)
        if saved:
            created.append({"key": saved.get("key", ""), "title": task.get("title", "")})
            continue

        # 只允许映射表中的有限 Issue Type，未知模型值降级为通用 Task。
        issue_type = ISSUETYPE_MAP.get(str(task.get("type", "task")).lower(), "Task")
        desc = (
            f"{task.get('description', '')}\n\n验收标准：{task.get('acceptance_criteria', '')}\n"
            f"故事点：{task.get('story_points', '')}"
        )
        key = await client.create_issue(
            project=project,
            summary=task.get("title", "未命名任务"),
            description=desc,
            issue_type=issue_type,
            parent_key=epic_key,
            idempotency_label=f"{idempotency_prefix}-{index}",
        )
        entry = {"key": key, "title": task.get("title", "")}
        if on_created is not None:
            await on_created(index, entry)
        created.append(entry)

    result = {
        "created": created,
        "dry_run": not client.configured,
        "project": project,
        "epic": epic_key,
    }
    logger.info("req_written", count=len(created), dry_run=result["dry_run"])
    return result


@lru_cache(maxsize=1)
def build_requirement_agent():
    """编译并缓存只包含 ``propose`` 的需求提议图。

    刻意不在图中添加 Jira 节点：图执行结束后可能等待数小时人工确认，进程内 checkpoint
    无法跨重启/多副本可靠保存；持久化两阶段 API 才是当前生产实现。
    """
    graph = StateGraph(ReqState)
    graph.add_node("propose", _propose)
    graph.add_edge(START, "propose")
    graph.add_edge("propose", END)
    logger.info("requirement_agent_build")
    return graph.compile()
