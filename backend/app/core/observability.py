"""Langfuse 可观测客户端的惰性初始化、请求回调和安全关闭。

Agent 构造 LangChain config 时调用 ``get_langfuse_callbacks()``；首次启用时初始化共享 client，
每次 run 创建独立 handler。FastAPI lifespan 退出阶段调用 ``close_observability`` flush 尾部
trace。未配置时返回空列表，因此业务可直接写 ``{"callbacks": get_langfuse_callbacks()}``。

生产边界：追踪是“尽力而为”的旁路能力，初始化、导出和关闭失败不得阻断业务或滚动退出；
同时这意味着监控平台必须对 trace 缺口单独告警。发送到 Langfuse 的 prompt/response 可能含
敏感数据，启用前必须完成数据分级、脱敏、访问控制和保留周期评审。
"""

import asyncio
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("observability")


@lru_cache(maxsize=1)
def _initialize_langfuse() -> Any | None:
    """初始化 Langfuse SDK。

    Langfuse 3/4 的 LangChain 回调通过全局 client registry 获取客户端，必须先显式
    创建客户端；只构造 ``CallbackHandler`` 会得到一个禁用的 handler。初始化结果做
    进程内缓存，避免每个请求重复创建 exporter 与后台线程。
    """
    settings = get_settings()
    if not settings.langfuse_enabled:
        return None

    try:
        from langfuse import Langfuse

        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_host,
            environment=settings.normalized_app_env,
        )
        logger.info(
            "langfuse_initialized",
            host=settings.langfuse_host,
            environment=settings.normalized_app_env,
        )
        return client
    except Exception as exc:  # noqa: BLE001 - 追踪不可影响业务请求
        logger.warning("langfuse_init_failed", error_type=type(exc).__name__)
        return None


def get_langfuse_callbacks() -> list[Any]:
    """返回可挂到 LangChain/LangGraph 的回调列表。

    未配置 Langfuse 时返回空列表，业务侧逻辑不受影响。
    """
    settings = get_settings()
    if not settings.langfuse_enabled or _initialize_langfuse() is None:
        return []

    try:
        # Langfuse 3/4 的公开导入路径。handler 保存单次 LangChain run 的映射，
        # 因此每次调用创建一个，底层 client/exporter 则由上面的 registry 复用。
        from langfuse.langchain import CallbackHandler

        handler = CallbackHandler(public_key=settings.langfuse_public_key)
        return [handler]
    except Exception as exc:  # noqa: BLE001 - 可观测失败不应阻断主流程
        logger.warning("langfuse_callback_failed", error_type=type(exc).__name__)
        return []


async def close_observability() -> None:
    """在进程退出前 flush 并关闭 Langfuse，避免滚动发布丢失尾部 trace。"""
    # 从未创建过 client 时不要在 shutdown 阶段反向初始化后台线程。
    if _initialize_langfuse.cache_info().currsize == 0:
        return
    client = _initialize_langfuse()
    if client is None:
        _initialize_langfuse.cache_clear()
        return

    try:
        await asyncio.to_thread(client.flush)
    except Exception as exc:  # noqa: BLE001 - 关闭可观测组件不能阻塞业务退出
        logger.warning("langfuse_flush_failed", error_type=type(exc).__name__)
    try:
        await asyncio.to_thread(client.shutdown)
    except Exception as exc:  # noqa: BLE001 - 关闭可观测组件不能阻塞业务退出
        logger.warning("langfuse_shutdown_failed", error_type=type(exc).__name__)
    finally:
        _initialize_langfuse.cache_clear()
