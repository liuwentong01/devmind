"""ARQ worker 启停顺序、PR 陈旧检查与 IM 通知可靠性测试。

worker 必须在消费前验证数据库；PR 审查必须在 LLM 调用后重新读取 head SHA；通知的
HTTP 失败或“200 + 业务错误”必须以 ``NotificationError`` 暴露给 ARQ 重试，并且日志与
异常都不能泄漏 webhook secret。

运行示例::

    cd backend
    .venv/bin/python -m unittest tests.test_worker_reliability -v

本模块用 AsyncMock 模拟 GitHub、模型和 HTTP 客户端，不需要 Redis 或外网。注意通知采用
至少一次语义：这些测试保证失败可见，并不声称超时重试绝不会重复发送。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.integrations.notifier import NotificationError, notify
from app.workers import rca_worker, review_worker, worker


class WorkerStartupTests(unittest.IsolatedAsyncioTestCase):
    """验证三个 worker 入口共享的启动防线和 Review 二次 SHA 检查。"""

    async def test_all_worker_entrypoints_validate_database_before_consuming(self) -> None:
        """专用/聚合入口的 startup 都必须 await ``init_db``。"""
        for module in (review_worker, rca_worker, worker):
            with self.subTest(module=module.__name__):
                init_db = AsyncMock()
                with (
                    patch.object(module, "init_db", init_db),
                    patch.object(module, "setup_logging"),
                    patch.object(module, "install_cost_tracking"),
                ):
                    await module._startup({})
                init_db.assert_awaited_once_with()

    async def test_review_rechecks_head_before_posting(self) -> None:
        """模型运行期间 PR 更新时返回 stale，绝不回写旧 diff 评论。"""
        old_head = "a" * 40
        new_head = "b" * 40
        github = AsyncMock()
        github.get_pull_head_sha.side_effect = [old_head, new_head]
        github.get_pull_diff.return_value = "diff --git a/a.py b/a.py"
        session_context = AsyncMock()
        session_context.__aenter__.return_value = object()
        session_context.__aexit__.return_value = None
        model = AsyncMock()
        model.ainvoke.return_value = SimpleNamespace(content="review")

        with (
            patch.object(review_worker, "GitHubClient", return_value=github),
            patch.object(review_worker, "SessionLocal", return_value=session_context),
            patch.object(review_worker, "load_review_rules", AsyncMock(return_value=[])),
            patch.object(review_worker, "build_review_messages", return_value=[]),
            patch.object(review_worker, "get_chat_model", return_value=model),
        ):
            result = await review_worker.review_pull_task({}, "owner", "repo", 7, old_head)

        self.assertEqual(result, "stale")
        github.post_review_once.assert_not_awaited()


class NotificationReliabilityTests(unittest.IsolatedAsyncioTestCase):
    """验证 IM 未配置、传输失败、业务错误与成功响应的统一契约。"""

    async def test_unconfigured_production_notification_fails_visible(self) -> None:
        """非 local 缺少 webhook 时必须抛错，而不是静默跳过。"""
        settings = SimpleNamespace(is_local=False, im_webhook_url="")
        with patch("app.integrations.notifier.get_settings", return_value=settings):
            with self.assertRaises(NotificationError):
                await notify("root cause")

    async def test_configured_send_failure_is_raised_for_arq_retry(self) -> None:
        """网络异常应转为无 secret/无异常链的可重试业务异常。"""
        secret = "super-secret-webhook-token"
        settings = SimpleNamespace(
            is_local=False,
            im_webhook_url=f"https://im.example/hook/{secret}",
        )
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.stream = Mock(side_effect=OSError(f"network down: {secret}"))
        with (
            patch("app.integrations.notifier.get_settings", return_value=settings),
            patch("app.integrations.notifier.httpx.AsyncClient", return_value=client),
            patch("app.integrations.notifier.logger.error") as log_error,
        ):
            with self.assertRaises(NotificationError) as raised:
                await notify("root cause")
        self.assertTrue(raised.exception.__suppress_context__)
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn(secret, repr(log_error.call_args))
        log_error.assert_called_once_with(
            "notify_failed",
            error_type="OSError",
            status_code=None,
        )

    async def test_feishu_http_200_business_error_is_retryable(self) -> None:
        """飞书 HTTP 200 但 code 非零仍应让 ARQ 重试。"""
        settings = SimpleNamespace(
            is_local=False,
            im_webhook_url="https://im.example/hook/token",
        )
        response = AsyncMock()
        response.__aenter__.return_value = response
        response.__aexit__.return_value = None
        response.headers = {"content-type": "application/json"}
        response.raise_for_status = Mock()

        async def body_chunks():
            """模拟流式业务失败响应。"""
            yield b'{"code":19002,"msg":"signature validation failed"}'

        response.aiter_bytes = body_chunks
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.stream = Mock(return_value=response)
        with (
            patch("app.integrations.notifier.get_settings", return_value=settings),
            patch("app.integrations.notifier.httpx.AsyncClient", return_value=client),
            patch("app.integrations.notifier.logger.error"),
        ):
            with self.assertRaises(NotificationError):
                await notify("root cause")

    async def test_feishu_and_slack_success_responses_are_accepted(self) -> None:
        """兼容飞书成功 JSON 与 Slack 的纯文本 ``ok``。"""
        settings = SimpleNamespace(
            is_local=False,
            im_webhook_url="https://im.example/hook/token",
        )
        for body, content_type in (
            (b'{"StatusCode":0,"StatusMessage":"success"}', "application/json"),
            (b"ok", "text/plain"),
        ):
            with self.subTest(body=body):
                response = AsyncMock()
                response.__aenter__.return_value = response
                response.__aexit__.return_value = None
                response.headers = {"content-type": content_type}
                response.raise_for_status = Mock()

                async def body_chunks(response_body=body):
                    """为当前 subTest 生成单块响应体。"""
                    yield response_body

                response.aiter_bytes = body_chunks
                client = AsyncMock()
                client.__aenter__.return_value = client
                client.__aexit__.return_value = None
                client.stream = Mock(return_value=response)
                with (
                    patch(
                        "app.integrations.notifier.get_settings",
                        return_value=settings,
                    ),
                    patch(
                        "app.integrations.notifier.httpx.AsyncClient",
                        return_value=client,
                    ),
                ):
                    self.assertTrue(await notify("root cause"))


if __name__ == "__main__":
    unittest.main()
