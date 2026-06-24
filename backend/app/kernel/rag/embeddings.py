"""Embedding：通过 LiteLLM 调用国内 embedding 模型，把文本转向量。"""

import litellm

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("rag.embeddings")


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量把文本转成向量。

    Args:
        texts: 待向量化的文本列表。

    Returns:
        与输入等长的向量列表。
    """
    if not texts:
        return []

    settings = get_settings()
    resp = await litellm.aembedding(model=settings.embedding_model, input=texts)
    # litellm 返回结构兼容 OpenAI：优先用 .data 属性，回退到下标；item 可能是 dict 或对象
    data = getattr(resp, "data", None)
    if data is None:
        data = resp["data"]
    vectors = [item["embedding"] if isinstance(item, dict) else item.embedding for item in data]
    logger.info("embed_done", count=len(vectors), dim=len(vectors[0]) if vectors else 0)
    return vectors


async def embed_query(text: str) -> list[float]:
    """把单条查询转成向量。"""
    vectors = await embed_texts([text])
    return vectors[0]
