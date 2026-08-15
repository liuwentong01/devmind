"""Redis 队列初始化、确定性 job id 与生产 RCA 降级语义测试。

覆盖多协程首次访问只能创建一个连接池、job id 不泄漏 payload、手动/ webhook 审查使用
同一去重域，以及生产证据源缺失时不得回退到 demo 数据。还验证“高置信度但验证失败”不能
提前结束 RCA，达到迭代上限后的结论必须包含人工复核警告。

运行示例::

    cd backend
    .venv/bin/python -m unittest tests.test_queue_and_rca_reliability -v

所有外部依赖都被 fake/mock；无需 Redis、Loki、Prometheus 或 GitHub。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.incident_rca.graph import (
    _conclusion_warning,
    _degraded_sources,
    _fetch_deploys,
    _route_after_verify,
)
from app.integrations.logs import _MOCK_LOGS, LogClient
from app.integrations.monitoring import _MOCK_METRICS, MonitoringClient
from app.kernel import queue


class _FakePool:
    """只实现关闭协议的 ARQ Redis pool fake。"""

    async def aclose(self) -> None:
        """模拟无异常释放连接池。"""
        return None


class QueueReliabilityTests(unittest.IsolatedAsyncioTestCase):
    """验证队列单例锁、连接关闭和稳定散列 job id。"""

    async def test_concurrent_first_use_creates_one_pool(self) -> None:
        """20 个并发首次调用必须共享一次 ``create_pool`` 结果。"""
        original_pool = queue._pool
        queue._pool = None
        create = AsyncMock(return_value=_FakePool())
        try:
            with patch.object(queue, "create_pool", create):
                pools = await __import__("asyncio").gather(*(queue.get_queue() for _ in range(20)))
            self.assertEqual(create.await_count, 1)
            self.assertTrue(all(pool is pools[0] for pool in pools))
            await queue.close_queue()
        finally:
            queue._pool = original_pool

    def test_job_ids_are_stable_and_payload_is_not_exposed(self) -> None:
        """相同输入得到相同散列 ID，输入变化会变更且敏感片段不明文出现。"""
        first = queue.make_job_id("review", "owner", "repo", 1, "secret-ish-sha")
        second = queue.make_job_id("review", "owner", "repo", 1, "secret-ish-sha")
        changed = queue.make_job_id("review", "owner", "repo", 1, "new-sha")
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)
        self.assertNotIn("secret-ish", first)

    def test_review_job_id_is_shared_by_manual_and_webhook_domains(self) -> None:
        """所有 Review 生产者必须调用统一 helper，避免跨入口重复任务。"""
        expected = queue.make_job_id("review", "owner", "repo", 7, "abc123")
        self.assertEqual(queue.review_job_id("owner", "repo", 7, "abc123"), expected)


class ProductionRcaFallbackTests(unittest.IsolatedAsyncioTestCase):
    """验证非 local RCA 数据源降级、路由阈值和操作员告警。"""

    async def test_unconfigured_production_sources_never_return_demo_evidence(self) -> None:
        """生产未配置三类证据源时应返回降级标记，绝不能混入 mock 内容。"""
        settings = SimpleNamespace(
            is_local=False,
            loki_url="",
            prometheus_url="",
            deploy_repo="",
        )
        with (
            patch("app.integrations.logs.get_settings", return_value=settings),
            patch("app.integrations.monitoring.get_settings", return_value=settings),
            patch("app.agents.incident_rca.graph.get_settings", return_value=settings),
        ):
            logs = await LogClient().query_logs("orders")
            metrics = await MonitoringClient().query_metrics("orders")
            deploys = (await _fetch_deploys({"alert": {}}))["deploys"]

        self.assertNotEqual(logs, _MOCK_LOGS)
        self.assertNotIn(_MOCK_METRICS.format(service="orders"), metrics)
        self.assertIn("未配置", logs)
        self.assertIn("未配置", metrics)
        self.assertIn("未配置", deploys)
        self.assertEqual(
            _degraded_sources({"logs": logs, "metrics": metrics, "deploys": deploys}),
            ["日志", "指标", "发布"],
        )

    def test_high_confidence_rejection_continues_until_iteration_limit(self) -> None:
        """验证结果明确为 false 时，高置信度也要继续分析至最大迭代。"""
        settings = SimpleNamespace(rca_confidence_threshold=0.75, rca_max_iterations=2)
        with patch("app.agents.incident_rca.graph.get_settings", return_value=settings):
            self.assertEqual(
                _route_after_verify(
                    {
                        "verification": {"confirmed": False},
                        "confidence": 0.95,
                        "iteration": 1,
                    }
                ),
                "analyze",
            )
            self.assertEqual(
                _route_after_verify(
                    {
                        "verification": {"confirmed": False},
                        "confidence": 0.95,
                        "iteration": 2,
                    }
                ),
                "conclude",
            )

    def test_unverified_conclusion_has_an_explicit_operator_warning(self) -> None:
        """低置信且未验证的结论必须提示尚未达标并要求人工复核。"""
        settings = SimpleNamespace(rca_confidence_threshold=0.75)
        with patch("app.agents.incident_rca.graph.get_settings", return_value=settings):
            warning = _conclusion_warning(
                {
                    "logs": "real logs",
                    "metrics": "real metrics",
                    "deploys": "real deploys",
                    "verification": {"confirmed": False},
                    "confidence": 0.6,
                }
            )
        self.assertIn("尚未通过验证阈值", warning)
        self.assertIn("人工复核", warning)


if __name__ == "__main__":
    unittest.main()
