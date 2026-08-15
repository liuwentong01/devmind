"""Embedding 批处理校验与 RAG 数据源隔离的可靠性测试。

模型供应商可能返回少向量、错误维度、零向量或乱序结果；这些问题若写进 pgvector，通常要
到检索阶段才暴露。本模块验证它们在数据库写入前 fail-closed，同时确认查询使用 provider
的 query 模式，并把 ``documents.source`` 过滤真正编译进 SQL。

运行示例::

    cd backend
    .venv/bin/python -m unittest tests.test_rag_reliability -v

测试 mock 掉模型与数据库执行，不产生网络流量。关键故障场景是“数量正确但顺序错”以及
Review Agent 意外检索到普通知识库内容，二者都会造成难以察觉的语义污染。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.code_review.reviewer import load_review_rules
from app.kernel.rag import embeddings
from app.kernel.rag.retriever import retrieve


class _Rows:
    """模拟 SQLAlchemy Result 中返回空行集合的接口。"""

    def all(self) -> list:
        """返回空检索结果，让测试只检查 SQL 结构。"""
        return []


class _CaptureSession:
    """捕获传给 ``session.execute`` 的 SQLAlchemy statement。"""

    def __init__(self) -> None:
        """初始化尚未执行任何语句的状态。"""
        self.statement = None

    async def execute(self, statement):  # noqa: ANN001
        """保存语句并返回最小空 Result fake。"""
        self.statement = statement
        return _Rows()


class EmbeddingReliabilityTests(unittest.IsolatedAsyncioTestCase):
    """验证 embedding 的批次、顺序、模式、数量和值域不变量。"""

    async def test_embeddings_are_batched_and_order_is_preserved(self) -> None:
        """45 条文本应拆为 20/20/5，拼接结果仍保持原输入顺序。"""
        calls: list[list[str]] = []

        async def fake_batch(
            texts: list[str], _model: str, _text_type: str, _api_key: str | None
        ) -> list[list[float]]:
            """用文本编号编码向量，便于断言跨批次顺序。"""
            calls.append(texts)
            return [[float(text.removeprefix("t")), 0.0, 1.0] for text in texts]

        settings = SimpleNamespace(
            embedding_model="test/model",
            embedding_dim=3,
            api_key_for_model=lambda _model: None,
        )
        with (
            patch.object(embeddings, "get_settings", return_value=settings),
            patch.object(embeddings, "_embed_batch", side_effect=fake_batch),
        ):
            vectors = await embeddings.embed_texts([f"t{i}" for i in range(45)])

        self.assertEqual([len(batch) for batch in calls], [20, 20, 5])
        self.assertEqual([vector[0] for vector in vectors], [float(i) for i in range(45)])

    async def test_embedding_count_mismatch_fails_before_database_write(self) -> None:
        """供应商少返回向量时必须显式报数量错误。"""
        settings = SimpleNamespace(
            embedding_model="test/model",
            embedding_dim=3,
            api_key_for_model=lambda _model: None,
        )
        with (
            patch.object(embeddings, "get_settings", return_value=settings),
            patch.object(
                embeddings,
                "_embed_batch",
                AsyncMock(return_value=[[0.0, 0.0, 0.0]]),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "返回数量"):
                await embeddings.embed_texts(["first", "second"])

    async def test_embedding_dimension_mismatch_is_explicit(self) -> None:
        """供应商向量维度与数据库 schema 不一致时必须显式失败。"""
        settings = SimpleNamespace(
            embedding_model="test/model",
            embedding_dim=3,
            api_key_for_model=lambda _model: None,
        )
        with (
            patch.object(embeddings, "get_settings", return_value=settings),
            patch.object(
                embeddings,
                "_embed_batch",
                AsyncMock(return_value=[[0.0, 0.0]]),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "维度"):
                await embeddings.embed_texts(["text"])

    async def test_zero_embedding_vector_is_rejected(self) -> None:
        """全零向量没有余弦方向，应在进入 pgvector 前被拒绝。"""
        settings = SimpleNamespace(
            embedding_model="test/model",
            embedding_dim=3,
            api_key_for_model=lambda _model: None,
        )
        with (
            patch.object(embeddings, "get_settings", return_value=settings),
            patch.object(
                embeddings,
                "_embed_batch",
                AsyncMock(return_value=[[0.0, 0.0, 0.0]]),
            ),
            self.assertRaisesRegex(ValueError, "零向量"),
        ):
            await embeddings.embed_texts(["text"])

    async def test_query_uses_provider_query_embedding_mode(self) -> None:
        """查询必须传 ``text_type=query`` 及由 Settings 解析的模型密钥。"""
        settings = SimpleNamespace(
            embedding_model="dashscope/test",
            embedding_dim=3,
            api_key_for_model=lambda _model: "dashscope-key",
        )
        embed_batch = AsyncMock(return_value=[[0.0, 0.0, 1.0]])
        with (
            patch.object(embeddings, "get_settings", return_value=settings),
            patch.object(embeddings, "_embed_batch", embed_batch),
        ):
            await embeddings.embed_query("question")
        self.assertEqual(embed_batch.await_args.args[2], "query")
        self.assertEqual(embed_batch.await_args.args[3], "dashscope-key")


class RetrievalIsolationTests(unittest.IsolatedAsyncioTestCase):
    """验证向量 SQL 和 Review 规则加载器都强制 source 隔离。"""

    async def test_source_filter_is_part_of_vector_query(self) -> None:
        """传入 source 时生成 SQL 必须包含列谓词及对应绑定参数。"""
        session = _CaptureSession()
        with patch(
            "app.kernel.rag.retriever.embed_query",
            AsyncMock(return_value=[0.0] * 1024),
        ):
            self.assertEqual(await retrieve(session, "query", top_k=4, source="review-rule"), [])

        compiled = session.statement.compile()
        self.assertIn("documents.source", str(compiled))
        self.assertIn("review-rule", compiled.params.values())

    async def test_review_rule_loader_requests_only_configured_source(self) -> None:
        """Review 只能检索配置的数据源，并准确传递 top_k。"""
        settings = SimpleNamespace(review_top_k=3, review_rule_source="team-rules")
        mocked_retrieve = AsyncMock(return_value=[])
        with (
            patch("app.agents.code_review.reviewer.get_settings", return_value=settings),
            patch("app.agents.code_review.reviewer.retrieve", mocked_retrieve),
        ):
            self.assertEqual(await load_review_rules(object(), "diff"), [])

        self.assertEqual(mocked_retrieve.await_args.kwargs["source"], "team-rules")
        self.assertEqual(mocked_retrieve.await_args.kwargs["top_k"], 3)


if __name__ == "__main__":
    unittest.main()
