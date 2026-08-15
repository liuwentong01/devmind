"""管理面 API：查询和重置跨副本 LLM 成本聚合。

路由由 ``app.main`` 以管理 API key 依赖挂载到 ``/api/admin``。示例：
``curl -H 'X-API-Key: <管理密钥>' http://localhost:8000/api/admin/cost``；重置使用
``curl -X POST -H 'X-API-Key: <管理密钥>' .../api/admin/cost/reset``。

生产边界：reset 会改变全局治理数据，因此普通 API key 会被拒绝。成本来自模型 SDK 的估算
价格与 token 回调，不是供应商账单；Redis 不可用、未知模型价格或回调缺失都可能造成偏差，
财务结算必须以供应商账单为准并对监控缺口告警。
"""

from fastapi import APIRouter

from app.kernel.cost import tracker

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/cost")
async def get_cost() -> dict:
    """从配置的 memory/Redis backend 返回累计 token、调用次数与估算费用。"""
    return await tracker.snapshot_for_backend()


@router.post("/cost/reset")
async def reset_cost() -> dict:
    """清空当前成本 backend 的聚合值；不会修改模型供应商账单。"""
    await tracker.reset_for_backend()
    return {"status": "reset"}
