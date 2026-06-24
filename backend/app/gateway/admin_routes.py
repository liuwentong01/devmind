"""管理 API：成本治理统计。"""

from fastapi import APIRouter

from app.kernel.cost import tracker

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/cost")
async def get_cost() -> dict:
    """返回当前进程累计的 LLM 用量与费用（按模型分组）。"""
    return tracker.snapshot()


@router.post("/cost/reset")
async def reset_cost() -> dict:
    """重置成本计数。"""
    tracker.reset()
    return {"status": "reset"}
