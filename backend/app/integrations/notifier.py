"""IM Webhook 通知适配器：把 RCA 结论推送到飞书或 Slack。

飞书和 Slack 都接受 ``text`` 语义但消息体略有差异，因此一次请求同时携带飞书的
``msg_type/content`` 与 Slack 的顶层 ``text``。Webhook URL 往往在 path/query 中包含
secret：本模块从 Settings 读取地址，并刻意不记录 URL、响应正文、异常字符串或堆栈。

HTTP 客户端设置连接 5 秒、总请求 10 秒和 2 次传输重试；响应最多 64 KiB。除 HTTP 状态
外还解析常见的“HTTP 200 + 业务错误码”，统一抛出 :class:`NotificationError`，让 ARQ
执行任务级重试。由于服务端可能已收消息但客户端超时，通知是至少一次语义，接收端仍应
容忍偶发重复。

local 未配置时返回 ``False`` 并记录长度，方便无 IM 环境开发；非 local 未配置时必须
fail-closed。调用示例::

    delivered = await notify("🔥 根因分析：checkout\n\n疑似连接池耗尽")
    # True 表示提供方确认成功；生产失败会抛 NotificationError，而非返回 False。
"""

import json

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("integrations.notifier")
MAX_NOTIFY_RESPONSE_BYTES = 64 * 1024


class NotificationError(RuntimeError):
    """已配置的生产通知无法确认送达，调用方应让队列按策略重试。"""


def _validate_provider_response(body: bytes, content_type: str) -> None:
    """识别飞书/Slack 在 HTTP 200 响应中的常见业务失败。

    Slack incoming webhook 通常返回纯文本 ``ok``；飞书常返回含 ``code`` 或
    ``StatusCode`` 的 JSON。未知的非 JSON 成功体保持兼容，但显式 ``ok=false`` 或非零
    错误码会转换为 :class:`NotificationError`。
    """
    stripped = body.strip()
    if not stripped:
        return
    if "json" not in content_type.lower() and not stripped.startswith(b"{"):
        # Slack incoming webhooks normally return the plain text ``ok``.
        return
    try:
        payload = json.loads(stripped)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    if not isinstance(payload, dict):
        return
    if payload.get("ok") is False:
        raise NotificationError("IM provider rejected notification")
    for field in ("code", "StatusCode", "statusCode"):
        if field in payload and payload[field] not in (0, "0", None):
            raise NotificationError("IM provider rejected notification")


async def notify(text: str) -> bool:
    """推送文本消息；生产发送失败必须向 worker 暴露以触发队列重试。

    Returns:
        成功确认发送时为 ``True``；仅 local 且未配置 webhook 时为 ``False``。

    Raises:
        NotificationError: 非 local 未配置，或传输/HTTP/响应大小/业务码失败。

    不要在调用方捕获后静默忽略此异常，否则 ARQ 会把未通知的任务误标为成功。
    """
    settings = get_settings()
    url = settings.im_webhook_url
    if not url:
        if settings.is_local:
            logger.info("notify_skipped", text_len=len(text))
            return False
        logger.error("notify_unconfigured")
        raise NotificationError("IM webhook is not configured")

    # 兼容飞书自定义机器人的 text 消息体；Slack 也接受 {"text": ...}
    payload = {"msg_type": "text", "content": {"text": text}, "text": text}
    try:
        transport = httpx.AsyncHTTPTransport(retries=2)
        timeout = httpx.Timeout(10, connect=5, pool=5)
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            async with client.stream("POST", url, json=payload) as resp:
                resp.raise_for_status()
                response_body = bytearray()
                async for chunk in resp.aiter_bytes():
                    response_body.extend(chunk)
                    if len(response_body) > MAX_NOTIFY_RESPONSE_BYTES:
                        raise NotificationError("IM provider response is too large")
                _validate_provider_response(
                    bytes(response_body),
                    resp.headers.get("content-type", ""),
                )
        logger.info("notify_sent")
    except Exception as exc:  # noqa: BLE001 - 统一转换为可重试的业务异常
        # Webhook URL 通常在 path/query 内携带 secret。httpx 异常可能包含完整
        # request URL，因此既不记录 ``str(exc)``/堆栈，也不保留异常链。
        status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        logger.error(
            "notify_failed",
            error_type=type(exc).__name__,
            status_code=status_code,
        )
        raise NotificationError("IM notification failed") from None
    return True
