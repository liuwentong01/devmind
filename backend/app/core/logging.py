"""全进程结构化日志配置与 logger 工厂。

``app.main.lifespan`` 和 worker 启动时调用 ``setup_logging``；业务代码使用
``logger = get_logger("component")``，再以 ``logger.info("event", field=value)`` 记录事件。
本地输出便于阅读的彩色文本，非本地输出可被 Loki/ELK 解析的单行 JSON。

生产边界：日志字段不得包含 API key、token、请求正文、PRD、diff 或带密钥的完整 URL；
异常对客户端应脱敏，但服务端可用 ``logger.exception`` 留下 stack trace。平台还需配置采集、
保留周期、访问权限和告警，本模块本身不提供审计日志的不可篡改保证。
"""

import logging
import sys

import structlog

from app.core.config import get_settings


def setup_logging() -> None:
    """初始化标准库 logging 与 structlog；应在处理首个请求/任务前调用一次。"""
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.ExceptionRenderer(),
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
    """获取带组件名的结构化 logger；调用本身不会创建文件或网络连接。"""
    return structlog.get_logger(name)
