"""DevMind 的跨业务基础设施包。

``core`` 包含配置、数据库、日志、可观测性和 HTTP 安全边界，供 gateway、agent、
kernel 与 worker 共同使用。典型导入为 ``from app.core.config import get_settings``；
应用进程的装配入口仍是 ``app.main``，不要从本包 ``__init__`` 隐式初始化资源。

生产边界：本包只承载所有业务都必须遵守的基础规则，不放具体 Agent 流程；新增全局
组件时应同时考虑多副本、超时、关闭顺序、敏感信息和非本地 fail-closed 行为。
"""
