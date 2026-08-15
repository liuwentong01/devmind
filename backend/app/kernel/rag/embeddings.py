"""Embedding：把文本转向量。"""

import os

import httpx
import litellm

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("rag.embeddings")


async def _embed_dashscope(texts: list[str], model: str) -> list[list[float]]:
    """直接调用 DashScope embedding，避免 aiohttp 误走本地代理导致 TLS 卡住。"""
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError("DASHSCOPE_API_KEY 未配置，无法调用 DashScope embedding")

    dashscope_model = model.removeprefix("dashscope/")
    payload = {
        "model": dashscope_model,
        "input": {"texts": texts},
        "parameters": {"text_type": "document"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        response = await client.post(
            "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()

    body = response.json()
    embeddings = body.get("output", {}).get("embeddings", [])
    embeddings.sort(key=lambda item: item.get("text_index", 0))
    return [item["embedding"] for item in embeddings]


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
    if settings.embedding_model.startswith("dashscope/"):
        vectors = await _embed_dashscope(texts, settings.embedding_model)
    else:
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
