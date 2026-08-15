"""所有 Server-Sent Events 响应共享的生产传输策略。

业务路由把异步生成器交给 ``stream_response``，例如
``return stream_response(_stream_agent(request))``；包装器设置反代理禁用缓冲、15 秒心跳、
30 秒慢客户端发送超时以及配置化总生命周期。事件格式由 ``sse-starlette`` 编码。

生产边界：SSE 是单向长连接，客户端应识别 ``done/error`` 并处理网络中断；当前业务事件不
提供可重放 event id，断线后只能重新发起请求。入口代理的 idle/read timeout 必须大于心跳
间隔，应用/worker 仍需独立的模型和上游超时，不能仅依赖连接总时限。
"""

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from sse_starlette.sse import EventSourceResponse

from app.core.config import get_settings

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
}


def stream_response(content: AsyncIterable[Any]) -> EventSourceResponse:
    """创建带心跳、代理头、慢客户端超时和总时限的 SSE 响应。"""
    return EventSourceResponse(
        _bounded_stream(content),
        headers=SSE_HEADERS,
        ping=15,
        send_timeout=30,
    )


async def _bounded_stream(content: AsyncIterable[Any]) -> AsyncIterator[Any]:
    """限制单条 SSE 的总处理时间；超时发送固定错误事件并关闭生成器。

    使用固定文案而非底层异常，避免把模型、数据库或网络实现细节暴露给客户端。
    """
    try:
        async with asyncio.timeout(get_settings().sse_max_duration_seconds):
            async for event in content:
                yield event
    except TimeoutError:
        yield {"event": "error", "data": '{"message":"处理超时，请稍后重试"}'}
