"""需求拆解 Agent：LangGraph 工作流，含 HITL 人工闸门。

流程：propose（LLM 拆任务）→ human_review（interrupt 暂停等人确认/编辑）→ write_jira。

HITL 用 LangGraph 的 interrupt() 实现：
- 第一次 ainvoke 跑到 human_review 时 interrupt，把提议任务抛出，图被 checkpointer 持久化挂起；
- 人工编辑后，用 Command(resume=approved_tasks) + 同一 thread_id 再次 ainvoke，从断点继续写 Jira。

P0 演示用 MemorySaver（单进程内存）；生产应换 Postgres checkpointer。
"""

import json
from functools import lru_cache
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.incident_rca.analyzer import parse_json
from app.core.config import get_settings
from app.core.logging import get_logger
from app.integrations.jira import ISSUETYPE_MAP, JiraClient
from app.kernel.llm.gateway import get_chat_model

logger = get_logger("agent.requirement")

PROPOSE_SYSTEM = (
    "你是研发项目经理。把 PRD 拆解为可执行的前后端开发任务。\n"
    "只输出 JSON 数组，每个元素形如："
    '{"title": "任务标题", "type": "backend|frontend", '
    '"description": "任务说明", "story_points": 1|2|3|5|8, '
    '"acceptance_criteria": "验收标准"}。\n'
    "拆解要点：按功能模块切分，前后端分离，粒度适中（单任务 ≤ 8 点）。不要输出 JSON 以外内容。"
)


class ReqState(TypedDict, total=False):
    prd: str
    project: str
    epic: str
    tasks: list[dict]  # LLM 提议
    approved_tasks: list[dict]  # 人工确认后
    result: dict


async def _propose(state: ReqState) -> ReqState:
    """LLM 拆解 PRD 为任务列表。"""
    model = get_chat_model()
    resp = await model.ainvoke(
        [SystemMessage(PROPOSE_SYSTEM), HumanMessage(f"【PRD】\n{state['prd']}")]
    )
    parsed = parse_json(str(resp.content))
    tasks = parsed if isinstance(parsed, list) else []
    logger.info("req_proposed", count=len(tasks))
    return {"tasks": tasks}


async def _human_review(state: ReqState) -> ReqState:
    """HITL 闸门：暂停，把提议任务交给人确认/编辑。

    interrupt 的返回值即人工通过 Command(resume=...) 传回的（已编辑）任务列表。
    """
    approved = interrupt({"tasks": state.get("tasks", [])})
    return {"approved_tasks": approved}


async def _write_jira(state: ReqState) -> ReqState:
    """把人工确认后的任务写入 Jira（未配置则 dry-run）。"""
    client = JiraClient()
    project = state.get("project") or get_settings().jira_default_project or "DEV"
    epic = state.get("epic") or None

    created = []
    for task in state.get("approved_tasks", []):
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
            parent_key=epic,
        )
        created.append({"key": key, "title": task.get("title", "")})

    result = {
        "created": created,
        "dry_run": not client.configured,
        "project": project,
        "epic": epic,
    }
    logger.info("req_written", count=len(created), dry_run=result["dry_run"])
    return {"result": result}


@lru_cache(maxsize=1)
def build_requirement_agent():
    """构建并编译需求拆解工作流（带 MemorySaver 以支持 HITL 断点恢复）。"""
    graph = StateGraph(ReqState)
    graph.add_node("propose", _propose)
    graph.add_node("human_review", _human_review)
    graph.add_node("write_jira", _write_jira)
    graph.add_edge(START, "propose")
    graph.add_edge("propose", "human_review")
    graph.add_edge("human_review", "write_jira")
    graph.add_edge("write_jira", END)
    logger.info("requirement_agent_build")
    return graph.compile(checkpointer=MemorySaver())


def extract_interrupt_tasks(result: dict) -> list[dict]:
    """从 ainvoke 返回结果中取出 interrupt 抛出的提议任务。"""
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return []
    payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
    if isinstance(payload, str):
        payload = parse_json(payload) or {}
    return payload.get("tasks", []) if isinstance(payload, dict) else []
