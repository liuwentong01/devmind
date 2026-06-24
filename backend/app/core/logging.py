"""结构化日志：用 structlog 输出 JSON / 彩色控制台日志。"""

import logging
import sys

import structlog

from app.core.config import get_settings


def setup_logging() -> None:
    """初始化 structlog。local 环境用彩色控制台，其它环境输出 JSON。"""
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if settings.app_env == "local":
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "devmind") -> structlog.stdlib.BoundLogger:
    """获取一个带名字的 logger。"""
    return structlog.get_logger(name)
