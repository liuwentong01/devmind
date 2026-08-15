"""系统健康探针与通用研发 Agent 的 SSE 对话入口。

``GET /api/health/live`` 只证明进程事件循环可响应；``/api/health/ready`` 额外验证数据库，供
Kubernetes 决定是否接流量。``POST /api/chat/stream`` 接收对话历史，例如
``{"messages":[{"role":"user","content":"计算 2*(3+4)"}]}``，并返回
``token/tool/done/error`` 事件。

生产边界：探针故意不要求 API key，但不得暴露版本、配置或下游拓扑；readiness 目前只覆盖
硬依赖数据库，不代表所有可选集成都健康。聊天输入不可信，Agent 只能使用注册的最小权限
工具；SSE 开始后不能再改 HTTP 状态码，因此运行错误以固定 error 事件返回并在服务端记录。
"""

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.core.config import get_settings
from app.core.db import check_db
from app.core.logging import get_logger
from app.core.observability import get_langfuse_callbacks
from app.gateway.sse import stream_response
from app.kernel.orchestrator.graph import build_agent
from app.schemas.chat import ChatRequest

logger = get_logger("gateway")
router = APIRouter()
health_router = APIRouter()


@health_router.get("/health", tags=["system"])
@health_router.get("/health/live", tags=["system"])
async def health() -> dict:
    """无下游依赖的 liveness；失败通常意味着应重启进程。"""
    return {"status": "ok"}


@health_router.get("/health/ready", tags=["system"])
async def readiness() -> dict:
    """验证数据库后返回 readiness；503 会让编排器暂时摘除该副本。"""
    try:
        await check_db()
    except Exception:  # noqa: BLE001
        logger.exception("readiness_check_failed")
        raise HTTPException(status_code=503, detail="服务尚未就绪") from None
    return {"status": "ready"}


async def _stream_agent(req: ChatRequest) -> AsyncGenerator[dict, None]:
    """运行 orchestrator，并只公开模型 token 与工具名称两类过程事件。

    Langfuse callback 和 recursion limit 通过每次 run 的 config 注入；不把完整工具参数/结果
    发给客户端，避免内部上下文或凭据经调试事件泄露。
    """
    agent = build_agent()
    inputs = {"messages": [{"role": m.role, "content": m.content} for m in req.messages]}
    config = {
        "callbacks": get_langfuse_callbacks(),
        "recursion_limit": get_settings().agent_recursion_limit,
    }

    try:
        async for event in agent.astream_events(inputs, version="v2", config=config):
            kind = event["event"]

            # LLM 增量 token
            if kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                text = getattr(chunk, "content", "") or ""
                if text:
                    yield {"event": "token", "data": text}

            # Agent 开始调用工具
            elif kind == "on_tool_start":
                tool_name = event.get("name", "unknown")
                logger.info("tool_start", tool=tool_name)
                yield {"event": "tool", "data": tool_name}

        yield {"event": "done", "data": "[DONE]"}
    except Exception:  # noqa: BLE001 - SSE 已开始，只能用事件返回固定错误文案
        logger.exception("agent_stream_error")
        yield {
            "event": "error",
            "data": json.dumps({"message": "对话处理失败，请稍后重试"}, ensure_ascii=False),
        }


@router.post("/chat/stream", tags=["chat"])
async def chat_stream(req: ChatRequest) -> EventSourceResponse:
    """流式对话接口（Server-Sent Events）。

    前端用 fetch 读取流，按 event 类型分别处理 token / tool / done / error；统一的心跳、
    发送超时和总时长由 ``stream_response`` 包装，不要直接返回裸生成器。
    """
    return stream_response(_stream_agent(req))
