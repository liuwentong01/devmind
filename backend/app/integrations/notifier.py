"""IM 通知：把根因结论推送到飞书/Slack 群机器人 webhook。"""

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("integrations.notifier")


async def notify(text: str) -> None:
    """推送一条文本消息到 IM。未配置 webhook 时只记日志。"""
    url = get_settings().im_webhook_url
    if not url:
        logger.info("notify_skipped", preview=text[:80])
        return

    # 兼容飞书自定义机器人的 text 消息体；Slack 也接受 {"text": ...}
    payload = {"msg_type": "text", "content": {"text": text}, "text": text}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
        logger.info("notify_sent")
    except Exception as exc:  # noqa: BLE001 - 通知失败不应阻断主流程
        logger.warning("notify_failed", error=str(exc))
