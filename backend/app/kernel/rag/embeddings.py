"""Embedding 供应商适配、批处理与向量契约校验。

架构位置：RAG 入库把文档切片交给 ``embed_texts(text_type="document")``，检索器通过
``embed_query`` 生成 query 向量。DashScope 使用专用 httpx 请求以避免代理/TLS 问题，其他
模型走 LiteLLM 通用接口；供应商密钥统一从 Settings 显式取得。

典型调用示例::

    document_vectors = await embed_texts(["第一段", "第二段"])
    query_vector = await embed_query("如何回滚？")

外部响应是不可信数据：本模块验证返回数量、可选 index 顺序、向量容器、数据库配置维度、
数值类型/有限性、非零与幅度。任一批失败就整体抛错，调用方不得写入部分向量。固定 20 条
小批次控制供应商限制和内存峰值，但没有并行发批，优先稳定与限流。``embedding_dim`` 必须
与 pgvector 列迁移一致；更换模型/维度需要迁移并完整重建索引。当前不提供本地缓存、模型
双写或自动重索引，这些属于未来路线。
"""

import math
from collections.abc import Sequence
from typing import Literal

import httpx
import litellm

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("rag.embeddings")

# DashScope 等 embedding API 通常对单批文本数有限制；固定小批次也避免一次请求/响应
# 在进程内制造过高峰值内存。调用方的总输入仍由 HTTP schema 限制。
EMBEDDING_BATCH_SIZE = 20
MAX_EMBEDDING_ABS_VALUE = 1_000_000.0


async def _embed_dashscope(
    texts: list[str],
    model: str,
    text_type: Literal["document", "query"],
    api_key: str | None,
) -> list[list[float]]:
    """直接调用 DashScope 文本向量接口并恢复输入顺序。

    ``trust_env=False`` 刻意忽略进程代理环境，避免 aiohttp/代理导致的 TLS 卡住，也意味着
    必须经企业代理出网的环境需要显式网络方案。响应 ``text_index`` 必须完整覆盖
    ``0..len(texts)-1``，否则拒绝把错序向量绑定到文档切片。
    """
    if not api_key:
        raise ValueError("DASHSCOPE_API_KEY 未配置，无法调用 DashScope embedding")

    dashscope_model = model.removeprefix("dashscope/")
    payload = {
        "model": dashscope_model,
        "input": {"texts": texts},
        "parameters": {"text_type": text_type},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    transport = httpx.AsyncHTTPTransport(retries=2)
    timeout = httpx.Timeout(30, connect=5, pool=5)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False, transport=transport) as client:
        response = await client.post(
            "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()

    body = response.json()
    embeddings = body.get("output", {}).get("embeddings", [])
    if not isinstance(embeddings, list):
        raise ValueError("Embedding 服务返回格式错误")
    if any(not isinstance(item, dict) for item in embeddings):
        raise ValueError("Embedding 服务返回格式错误")
    embeddings.sort(key=lambda item: item.get("text_index", -1))
    if [item.get("text_index") for item in embeddings] != list(range(len(texts))):
        raise ValueError("Embedding 服务返回的文本索引不完整")
    try:
        return [item["embedding"] for item in embeddings]
    except (KeyError, TypeError) as exc:
        raise ValueError("Embedding 服务返回格式错误") from exc


async def _embed_batch(
    texts: list[str],
    model: str,
    text_type: Literal["document", "query"],
    api_key: str | None,
) -> list[list[float]]:
    """调用单批供应商 API，并把多版本响应统一为向量列表。

    LiteLLM item 若提供 ``index`` 就排序并严格验证；完全不提供 index 时按供应商返回顺序
    接受，后续公共校验仍会核对数量/维度。网络、认证和 HTTP 错误向上传播。
    """
    if model.startswith("dashscope/"):
        return await _embed_dashscope(texts, model, text_type, api_key)

    kwargs = {
        "model": model,
        "input": texts,
        "timeout": 30,
        "max_retries": 2,
    }
    if api_key:
        kwargs["api_key"] = api_key
    resp = await litellm.aembedding(**kwargs)
    # litellm 返回结构兼容 OpenAI：优先用 .data 属性，回退到下标；item 可能是 dict 或对象
    data = getattr(resp, "data", None)
    if data is None:
        data = resp["data"]
    items = list(data)
    indexes = [
        item.get("index") if isinstance(item, dict) else getattr(item, "index", None)
        for item in items
    ]
    if any(index is not None for index in indexes):
        if any(isinstance(index, bool) or not isinstance(index, int) for index in indexes):
            raise ValueError("Embedding 服务返回的文本索引无效")
        ordered = sorted(zip(indexes, items, strict=True), key=lambda pair: pair[0])
        if [index for index, _item in ordered] != list(range(len(texts))):
            raise ValueError("Embedding 服务返回的文本索引不完整")
        items = [item for _index, item in ordered]
    try:
        return [item["embedding"] if isinstance(item, dict) else item.embedding for item in items]
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Embedding 服务返回格式错误") from exc


async def embed_texts(
    texts: list[str], *, text_type: Literal["document", "query"] = "document"
) -> list[list[float]]:
    """批量把文本转成向量。

    Args:
        texts: 待向量化的文本列表。

    Returns:
        与输入等长的向量列表。

    Raises:
        ValueError: 响应数量、索引、结构、维度或数值不符合契约。
        httpx.HTTPError: DashScope 网络/HTTP 失败。
        Exception: LiteLLM/其他供应商调用失败。

    空列表不会访问供应商并直接返回空列表；非空输入按固定批次串行发送，最终再次检查总
    数量，防止未来实现变更造成静默丢批。
    """
    if not texts:
        return []

    settings = get_settings()
    api_key = settings.api_key_for_model(settings.embedding_model)
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        batch = texts[start : start + EMBEDDING_BATCH_SIZE]
        batch_vectors = await _embed_batch(batch, settings.embedding_model, text_type, api_key)
        if len(batch_vectors) != len(batch):
            raise ValueError(
                "Embedding 服务返回数量与输入不一致："
                f"expected={len(batch)}, actual={len(batch_vectors)}"
            )
        vectors.extend(batch_vectors)

    if len(vectors) != len(texts):
        raise ValueError(
            f"Embedding 服务返回数量与输入不一致：expected={len(texts)}, actual={len(vectors)}"
        )
    if any(
        not isinstance(vector, Sequence) or isinstance(vector, (str, bytes)) for vector in vectors
    ):
        raise ValueError("Embedding 服务返回了非向量数据")
    invalid_dims = [len(vector) for vector in vectors if len(vector) != settings.embedding_dim]
    if invalid_dims:
        raise ValueError(
            "Embedding 维度与数据库配置不一致："
            f"expected={settings.embedding_dim}, actual={invalid_dims[0]}"
        )
    try:
        normalized = [[float(value) for value in vector] for vector in vectors]
    except (TypeError, ValueError) as exc:
        raise ValueError("Embedding 向量包含非数值元素") from exc
    if any(not math.isfinite(value) for vector in normalized for value in vector):
        raise ValueError("Embedding 向量包含非有限数值")
    if any(
        not any(value != 0.0 for value in vector)
        or any(abs(value) > MAX_EMBEDDING_ABS_VALUE for value in vector)
        for vector in normalized
    ):
        raise ValueError("Embedding 向量为零向量或数值超出安全范围")
    logger.info("embed_done", count=len(normalized), dim=len(normalized[0]) if normalized else 0)
    return normalized


async def embed_query(text: str) -> list[float]:
    """以供应商的 query 模式把单条检索文本转成一个合规向量。

    document/query 模式可能使用不同训练语义，不能用默认 document 模式替代。供应商返回空
    结果会明确失败，避免构造无法检索的 SQL。
    """
    vectors = await embed_texts([text], text_type="query")
    if not vectors:
        raise ValueError("Embedding 服务未返回查询向量")
    return vectors[0]
