"""需求拆解 API（HITL 两阶段）：

1. POST /requirement/plan    —— 读 PRD → LLM 拆任务 → 在 human_review 处 interrupt 暂停，返回提议
2. POST /requirement/confirm —— 携带人工编辑后的任务，Command(resume) 恢复 → 写 Jira

人工闸门体现在：写 Jira 永远发生在 confirm 之后，plan 阶段绝不落库。
"""

import uuid

from fastapi import APIRouter, HTTPException
from langgraph.types import Command

from app.agents.requirement.graph import build_requirement_agent, extract_interrupt_tasks
from app.core.logging import get_logger
from app.integrations.confluence import ConfluenceClient
from app.schemas.requirement import (
    ConfirmRequest,
    PlanRequest,
    PlanResponse,
)

logger = get_logger("gateway.requirement")
router = APIRouter(prefix="/requirement", tags=["requirement"])


@router.post("/plan", response_model=PlanResponse)
async def plan(req: PlanRequest) -> PlanResponse:
    """拆解 PRD，返回提议任务（HITL 暂停，未写 Jira）。"""
    prd = req.prd_text.strip()
    if not prd and req.confluence_url.strip():
        try:
            prd = await ConfluenceClient().get_page_text(req.confluence_url.strip())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"读取 Confluence 失败：{exc}") from exc

    agent = build_requirement_agent()
    thread_id = uuid.uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}
    result = await agent.ainvoke(
        {"prd": prd, "project": req.project, "epic": req.epic}, config
    )

    tasks = extract_interrupt_tasks(result)
    logger.info("req_plan_done", thread_id=thread_id, tasks=len(tasks))
    return PlanResponse(thread_id=thread_id, tasks=tasks)


@router.post("/confirm")
async def confirm(req: ConfirmRequest) -> dict:
    """人工确认后恢复工作流，写入 Jira。"""
    agent = build_requirement_agent()
    config = {"configurable": {"thread_id": req.thread_id}}
    approved = [t.model_dump() for t in req.tasks]
    try:
        final = await agent.ainvoke(Command(resume=approved), config)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"恢复工作流失败（thread_id 是否有效？）：{exc}") from exc

    result = final.get("result")
    if not result:
        raise HTTPException(status_code=500, detail="未产出写入结果")
    return result
