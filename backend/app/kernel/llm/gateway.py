"""LLM 网关：用 LiteLLM 向业务层隐藏供应商差异与密钥装配。

架构位置：所有 Agent 和通用编排器都应通过 ``get_chat_model`` 获取 ChatModel，而不是直接
实例化供应商 SDK。网关从强校验 Settings 读取默认模型、密钥、超时、重试、最大输出并
返回 LangChain/LangGraph 兼容的 ``ChatLiteLLM``；成本与链路追踪由全局 LiteLLM 回调观察。

典型调用示例::

    model = get_chat_model(temperature=0)
    response = await model.ainvoke([HumanMessage("用一句话概括变更")])
    async for chunk in model.astream([HumanMessage("逐步解释")]):
        consume(chunk.content)

状态与副作用：同一 ``(model, temperature)`` 的包装器在单进程内缓存，实际调用仍会产生
网络请求、token 成本和回调。密钥显式传入是为了让 Pydantic ``.env`` 配置生效，而不是
依赖第三方库再次扫描环境。供应商认证/超时会向上传播。当前只提供统一接入和 SDK 重试；
按场景选模、熔断、跨供应商 failover、用户限流与预算拒绝仍是未来路线。
"""

from functools import lru_cache

from langchain_litellm import ChatLiteLLM

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("llm.gateway")


@lru_cache(maxsize=8)
def get_chat_model(model: str | None = None, temperature: float | None = None) -> ChatLiteLLM:
    """获取一个 ChatLiteLLM 实例（带缓存，相同参数复用）。

    Args:
        model: LiteLLM 模型名，默认读配置（如 deepseek/deepseek-chat）。
        temperature: 采样温度，默认读配置。

    Returns:
        ChatLiteLLM，支持 LangGraph 的 bind_tools / 流式输出。

    示例::

        deterministic = get_chat_model("deepseek/deepseek-chat", temperature=0)

    Raises:
        ValueError: 配置的模型参数或显式覆盖值不被下游接受。

    注意 ``lru_cache`` 的 key 来自调用参数：省略温度与显式传入当前默认温度会得到两个等价
    包装器，这不影响语义。运行时修改环境变量也不会自动刷新已缓存实例；配置应在进程启动
    前完成，测试需要先清理该缓存。
    """
    settings = get_settings()
    resolved_model = model or settings.llm_default_model
    resolved_temp = settings.llm_temperature if temperature is None else temperature
    api_key = settings.api_key_for_model(resolved_model)

    logger.info(
        "llm_model_init",
        model=resolved_model,
        temperature=resolved_temp,
        timeout_seconds=settings.llm_request_timeout,
        max_retries=settings.llm_max_retries,
        max_tokens=settings.llm_max_tokens,
    )

    # 显式传入 Settings 已读取的 provider key，确保通过 .env 配置时也能工作，同时避免
    # 业务模块接触密钥。未纳入校验的自定义 provider 仍可使用工作负载身份或 SDK 原生认证。
    return ChatLiteLLM(
        model=resolved_model,
        api_key=api_key,
        temperature=resolved_temp,
        request_timeout=settings.llm_request_timeout,
        max_retries=settings.llm_max_retries,
        max_tokens=settings.llm_max_tokens,
        streaming=True,
    )
