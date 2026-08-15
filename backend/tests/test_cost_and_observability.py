"""分布式成本统计与 Langfuse 生命周期兼容性回归测试。

覆盖两个容易在多副本生产环境中失真的边界：成本计数必须通过 Redis pipeline 原子累加，
Redis 故障又不能反向打断模型响应；Langfuse v4 客户端必须先初始化、按环境打标，并在
worker/API 关闭时 flush 后 shutdown。

运行本文件::

    cd backend
    LITELLM_LOCAL_MODEL_COST_MAP=True .venv/bin/python -m unittest \
        tests.test_cost_and_observability -v

本模块使用内存 fake，不需要真实 Redis 或 Langfuse。关键故障场景包括并发计数丢失、
NaN 成本污染、回调重复注册和进程退出前 trace 未刷新。
"""

import asyncio
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import litellm

from app.core import observability
from app.kernel import cost


class _FakePipeline:
    """记录 Redis hash 原子累加命令的最小 pipeline fake。"""

    def __init__(self) -> None:
        """初始化按调用顺序保存的命令列表。"""
        self.commands: list[tuple] = []

    def hincrby(self, *args):  # noqa: ANN002, ANN201
        """记录整数累加并返回自身，以模拟 redis-py 链式 API。"""
        self.commands.append(("hincrby", *args))
        return self

    def hincrbyfloat(self, *args):  # noqa: ANN002, ANN201
        """记录浮点累加并返回自身。"""
        self.commands.append(("hincrbyfloat", *args))
        return self

    async def execute(self) -> list:
        """模拟提交 pipeline；断言只关心已记录命令。"""
        return []


class _FakeRedis:
    """为成本 tracker 提供 pipeline、快照和删除操作的 Redis fake。"""

    def __init__(self, snapshot: dict | None = None) -> None:
        """可注入 bytes key/value 快照，贴近真实 redis-py 返回形状。"""
        self.pipe = _FakePipeline()
        self.snapshot = snapshot or {}
        self.deleted: list[str] = []

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        """要求事务 pipeline，避免测试误接受非原子实现。"""
        assert transaction
        return self.pipe

    async def hgetall(self, _key: str) -> dict:
        """返回预置的跨副本共享 hash 快照。"""
        return self.snapshot

    async def delete(self, key: str) -> None:
        """记录 reset 删除的 Redis key。"""
        self.deleted.append(key)


class DistributedCostTests(unittest.IsolatedAsyncioTestCase):
    """验证 Redis 主存储、内存降级与 LiteLLM 回调注册契约。"""

    async def test_redis_backend_uses_atomic_hash_increments_and_shared_snapshot(self) -> None:
        """一次调用应以六个 hash 累加写入，并能读取/重置共享快照。"""
        settings = SimpleNamespace(
            resolved_cost_tracking_backend="redis",
            cost_redis_key="devmind:cost:test",
        )
        redis = _FakeRedis(
            {
                b"total:requests": b"2",
                b"total:cost_usd": b"0.25",
                b"model:deepseek/chat:requests": b"2",
                b"model:deepseek/chat:prompt_tokens": b"10",
                b"model:deepseek/chat:completion_tokens": b"5",
                b"model:deepseek/chat:cost_usd": b"0.25",
            }
        )
        tracker = cost.CostTracker()
        with (
            patch.object(cost, "get_settings", return_value=settings),
            patch.object(cost, "get_queue", new=AsyncMock(return_value=redis)),
        ):
            await tracker.record_for_backend("deepseek/chat", 10, 5, 0.25)
            snapshot = await tracker.snapshot_for_backend()
            await tracker.reset_for_backend()

        self.assertEqual(len(redis.pipe.commands), 6)
        self.assertEqual(snapshot["total_requests"], 2)
        self.assertEqual(snapshot["by_model"]["deepseek/chat"]["total_tokens"], 15)
        self.assertEqual(snapshot["storage"], "redis")
        self.assertFalse(snapshot["degraded"])
        self.assertEqual(redis.deleted, ["devmind:cost:test"])

    async def test_redis_failure_falls_back_without_failing_model_callback(self) -> None:
        """Redis 不可用时改用进程内统计，NaN 成本归零且模型回调不抛错。"""
        settings = SimpleNamespace(
            resolved_cost_tracking_backend="redis",
            cost_redis_key="devmind:cost:test",
        )
        tracker = cost.CostTracker()
        with (
            patch.object(cost, "get_settings", return_value=settings),
            patch.object(cost, "get_queue", new=AsyncMock(side_effect=OSError("down"))),
        ):
            await tracker.record_for_backend("model", 3, 2, float("nan"))
            snapshot = await tracker.snapshot_for_backend()

        self.assertEqual(snapshot["total_requests"], 1)
        self.assertEqual(snapshot["total_cost_usd"], 0.0)
        self.assertEqual(snapshot["storage"], "memory-fallback")
        self.assertTrue(snapshot["degraded"])

    def test_callback_registration_matches_configured_backend(self) -> None:
        """Redis 后端只注册异步回调，重复安装也不能造成重复计费。"""
        original_sync = list(litellm.success_callback)
        original_async = list(litellm._async_success_callback)
        try:
            litellm.success_callback.clear()
            litellm._async_success_callback.clear()
            with patch.object(
                cost,
                "get_settings",
                return_value=SimpleNamespace(resolved_cost_tracking_backend="redis"),
            ):
                cost.install_cost_tracking()
                cost.install_cost_tracking()
            self.assertNotIn(cost._success_callback, litellm.success_callback)
            self.assertEqual(litellm._async_success_callback.count(cost._async_success_callback), 1)
        finally:
            litellm.success_callback[:] = original_sync
            litellm._async_success_callback[:] = original_async


class ObservabilityCompatibilityTests(unittest.TestCase):
    """验证 Langfuse v4 初始化参数、缓存边界和优雅关闭顺序。"""

    def tearDown(self) -> None:
        """清除进程级初始化缓存，隔离不同测试构造的 fake 模块。"""
        observability._initialize_langfuse.cache_clear()

    def test_langfuse_v4_client_is_initialized_before_callback(self) -> None:
        """客户端只初始化一次，而每次请求获得独立 CallbackHandler。"""
        created_clients: list[dict] = []
        created_handlers: list[dict] = []

        langfuse_module = ModuleType("langfuse")
        langchain_module = ModuleType("langfuse.langchain")

        class FakeLangfuse:
            """记录 v4 Langfuse 客户端初始化关键字参数。"""

            def __init__(self, **kwargs) -> None:  # noqa: ANN003
                """保存参数，验证 base_url/environment 映射。"""
                created_clients.append(kwargs)

        class FakeHandler:
            """模拟每次调用新建的 LangChain CallbackHandler。"""

            def __init__(self, **kwargs) -> None:  # noqa: ANN003
                """记录 handler 参数并保留实例类型用于断言。"""
                created_handlers.append(kwargs)

        langfuse_module.Langfuse = FakeLangfuse
        langchain_module.CallbackHandler = FakeHandler
        settings = SimpleNamespace(
            langfuse_enabled=True,
            langfuse_public_key="pk-test",
            langfuse_secret_key="sk-test",
            langfuse_host="https://langfuse.example.com",
            normalized_app_env="staging",
        )

        observability._initialize_langfuse.cache_clear()
        with (
            patch.object(observability, "get_settings", return_value=settings),
            patch.dict(
                sys.modules,
                {"langfuse": langfuse_module, "langfuse.langchain": langchain_module},
            ),
        ):
            first = observability.get_langfuse_callbacks()
            second = observability.get_langfuse_callbacks()

        self.assertEqual(len(created_clients), 1)
        self.assertEqual(created_clients[0]["base_url"], "https://langfuse.example.com")
        self.assertEqual(created_clients[0]["environment"], "staging")
        self.assertEqual(len(created_handlers), 2)
        self.assertIsInstance(first[0], FakeHandler)
        self.assertIsInstance(second[0], FakeHandler)

    def test_close_flushes_and_shuts_down_initialized_client(self) -> None:
        """已初始化客户端关闭时必须先 flush、再 shutdown，并清空缓存。"""
        calls: list[str] = []

        class FakeClient:
            """记录可观测客户端关闭协议调用顺序。"""

            def flush(self) -> None:
                """模拟把内存 trace 推送到服务端。"""
                calls.append("flush")

            def shutdown(self) -> None:
                """模拟释放客户端线程/连接。"""
                calls.append("shutdown")

        with patch.object(observability, "_initialize_langfuse", return_value=FakeClient()) as init:
            init.cache_info.return_value.currsize = 1
            asyncio.run(observability.close_observability())

        self.assertEqual(calls, ["flush", "shutdown"])
        init.cache_clear.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
