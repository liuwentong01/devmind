"""外部 HTTP 响应的共享资源边界工具。

``httpx`` 的 ``response.json()`` 往往会一次性把整个响应读入内存，本模块提供的辅助函数
先按块读取，再解析 JSON。GitHub、Jira、Loki 和 Prometheus 客户端共同使用它，保证
大小限制不会因不同集成各自实现而漂移。

示例::

    async with client.stream("GET", url) as response:
        response.raise_for_status()
        payload = await read_bounded_json(response, max_bytes=2 * 1024 * 1024)

限制作用于 HTTP 解压后的字节流；因此压缩炸弹、chunked 响应以及缺失/错误的
``Content-Length`` 也会被真正的累计长度拦截。
"""

import json
from typing import Any

import httpx


class UpstreamResponseTooLarge(RuntimeError):
    """上游响应超过允许的解压后字节数。"""


async def read_bounded_json(response: httpx.Response, max_bytes: int) -> Any:
    """流式读取并解析 JSON，同时限制解压后的实际字节数。

    Args:
        response: 已通过 ``client.stream(...)`` 打开的 httpx 响应。
        max_bytes: 允许读取的最大正数字节数，由可信服务端配置提供。

    Returns:
        JSON 对应的 Python 对象；调用方仍需验证对象结构和字段类型。

    Raises:
        UpstreamResponseTooLarge: 响应头或实际读取内容超过上限。
        RuntimeError: 响应不是有效的 UTF-8 JSON。

    此函数不调用 ``raise_for_status``，因为调用方最了解哪些 HTTP 状态可接受。
    """
    content_length = response.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > max_bytes:
        raise UpstreamResponseTooLarge("upstream response is too large")

    raw = bytearray()
    async for chunk in response.aiter_bytes():
        raw.extend(chunk)
        if len(raw) > max_bytes:
            raise UpstreamResponseTooLarge("upstream response is too large")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("upstream returned invalid JSON") from exc
