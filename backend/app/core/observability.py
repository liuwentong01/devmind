"""可观测：提供 Langfuse 回调（未配置时返回空，调用方无需关心）。"""

from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("observability")


def get_langfuse_callbacks() -> list[Any]:
    """返回可挂到 LangChain/LangGraph 的回调列表。

    未配置 Langfuse 时返回空列表，业务侧逻辑不受影响。
    """
    settings = get_settings()
    if not settings.langfuse_enabled:
        return []

    try:
        from langfuse.callback import CallbackHandler

        handler = CallbackHandler(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        return [handler]
    except Exception as exc:  # noqa: BLE001 - 可观测失败不应阻断主流程
        logger.warning("langfuse_init_failed", error=str(exc))
        return []
