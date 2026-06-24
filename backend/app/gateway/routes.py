"""API 路由：健康检查 + SSE 流式对话。"""

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.core.logging import get_logger
from app.core.observability import get_langfuse_callbacks
from app.kernel.orchestrator.graph import build_agent
from app.schemas.chat import ChatRequest

logger = get_logger("gateway")
router = APIRouter()


@router.get("/health", tags=["system"])
async def health() -> dict:
    """健康检查。"""
    return {"status": "ok"}


async def _stream_agent(req: ChatRequest) -> AsyncGenerator[dict, None]:
    """运行 Agent 并把过程转成 SSE 事件流。"""
    agent = build_agent()
    inputs = {"messages": [{"role": m.role, "content": m.content} for m in req.messages]}
    config = {"callbacks": get_langfuse_callbacks()}

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
    except Exception as exc:  # noqa: BLE001 - 把错误透传给前端
        logger.error("agent_stream_error", error=str(exc))
        yield {"event": "error", "data": json.dumps({"message": str(exc)}, ensure_ascii=False)}


@router.post("/chat/stream", tags=["chat"])
async def chat_stream(req: ChatRequest) -> EventSourceResponse:
    """流式对话接口（Server-Sent Events）。

    前端用 fetch 读取流，按 event 类型分别处理 token / tool / done / error。
    """
    return EventSourceResponse(_stream_agent(req))
