"""LLM 网关：通过 LiteLLM 统一接入国内模型（DeepSeek / 通义 / 智谱）。

P0 只暴露「按模型名拿到一个可用于 LangGraph 的 Chat 模型」的能力，
后续可在此扩展：按场景路由、失败降级、成本统计、限流等。
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
    """
    settings = get_settings()
    resolved_model = model or settings.llm_default_model
    resolved_temp = settings.llm_temperature if temperature is None else temperature

    logger.info("llm_model_init", model=resolved_model, temperature=resolved_temp)

    # ChatLiteLLM 会把请求委托给 litellm，litellm 自动按模型名前缀
    # 读取对应厂商的 API Key 环境变量（DEEPSEEK_API_KEY 等）。
    return ChatLiteLLM(
        model=resolved_model,
        temperature=resolved_temp,
        streaming=True,
    )
