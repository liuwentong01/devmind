"""需求拆解持久化、Jira 幂等创建与租约保留策略测试。

需求确认可能在创建第 N 个 Jira Issue 后遇到网络/进程故障。恢复时必须复用已 checkpoint
的任务，只创建剩余项，并使用稳定 label 去重。本模块同时验证 dry-run key 的稳定性、
Jira 半配置 fail-closed、项目缺失时不产生副作用，以及清理任务不会删除仍活跃的租约。

运行示例::

    cd backend
    .venv/bin/python -m unittest tests.test_requirement_reliability -v

测试不连接 PostgreSQL/Jira，而是检查调用记录和 SQLAlchemy delete 谓词；真实并发租约与
上游 Jira 行为仍属于预生产集成测试范围。
"""

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.requirement.graph import write_jira_tasks
from app.integrations.jira import JiraClient, JiraConfigurationError
from app.kernel.requirement_store import cleanup_expired_plans


class _FakeJira:
    """记录 create_issue 参数并返回递增 key 的 Jira 客户端 fake。"""

    configured = True
    dry_run_allowed = False

    def __init__(self) -> None:
        """初始化空副作用记录。"""
        self.calls: list[dict] = []

    async def create_issue(self, **kwargs) -> str:  # noqa: ANN003
        """记录创建参数，用确定性顺序 key 模拟成功响应。"""
        self.calls.append(kwargs)
        return f"DEV-{len(self.calls) + 1}"


class RequirementReliabilityTests(unittest.IsolatedAsyncioTestCase):
    """验证需求批处理可恢复性、Jira 配置边界和过期清理。"""

    async def test_batch_resume_skips_persisted_tasks_and_checkpoints_each_new_issue(self) -> None:
        """恢复批次应跳过 index 0，并在每个新 Issue 后立即调用 checkpoint。"""
        jira = _FakeJira()
        saved: list[tuple[int, dict]] = []

        async def on_created(index: int, issue: dict) -> None:
            """模拟持久化层保存每一步进度。"""
            saved.append((index, issue))

        tasks = [
            {"title": "one", "type": "backend"},
            {"title": "two", "type": "backend"},
            {"title": "three", "type": "frontend"},
        ]
        progress = [{"task_index": 0, "key": "DEV-1", "title": "one"}]
        with patch("app.agents.requirement.graph.JiraClient", return_value=jira):
            result = await write_jira_tasks(
                tasks,
                project="DEV",
                idempotency_prefix="devmind-thread",
                progress=progress,
                on_created=on_created,
            )

        self.assertEqual([item["key"] for item in result["created"]], ["DEV-1", "DEV-2", "DEV-3"])
        self.assertEqual(
            [call["idempotency_label"] for call in jira.calls],
            ["devmind-thread-1", "devmind-thread-2"],
        )
        self.assertEqual([index for index, _issue in saved], [1, 2])

    async def test_dry_run_key_is_stable_across_client_instances(self) -> None:
        """相同 label 即使标题变化、客户端重建，也必须生成同一 DRY key。"""
        settings = SimpleNamespace(
            atlassian_base_url="",
            atlassian_email="",
            atlassian_api_token="",
            jira_dry_run_allowed=True,
            upstream_max_response_bytes=1024,
        )
        with patch("app.integrations.jira.get_settings", return_value=settings):
            first = await JiraClient().create_issue(
                "DEV", "title", "description", idempotency_label="devmind-thread-0"
            )
            second = await JiraClient().create_issue(
                "DEV", "a changed title", "description", idempotency_label="devmind-thread-0"
            )
        self.assertEqual(first, second)

    async def test_configured_jira_requires_explicit_or_default_project(self) -> None:
        """真实 Jira 已启用但无项目时必须在第一次 API 调用前失败。"""
        jira = _FakeJira()
        settings = SimpleNamespace(jira_default_project="")
        with (
            patch("app.agents.requirement.graph.JiraClient", return_value=jira),
            patch("app.agents.requirement.graph.get_settings", return_value=settings),
        ):
            with self.assertRaises(JiraConfigurationError):
                await write_jira_tasks(
                    [{"title": "one", "type": "backend"}],
                    idempotency_prefix="devmind-thread",
                )
        self.assertEqual(jira.calls, [])

    def test_partial_atlassian_credentials_fail_closed(self) -> None:
        """base/email/token 只配置一部分时不得误入 dry-run。"""
        settings = SimpleNamespace(
            atlassian_base_url="https://example.atlassian.net",
            atlassian_email="",
            atlassian_api_token="token",
            jira_dry_run_allowed=True,
            upstream_max_response_bytes=1024,
        )
        with patch("app.integrations.jira.get_settings", return_value=settings):
            with self.assertRaises(JiraConfigurationError):
                JiraClient()

    async def test_retention_cleanup_protects_non_stale_processing_leases(self) -> None:
        """清理谓词应只删除过期且非活跃、或处理租约已陈旧的计划。"""
        session = AsyncMock()
        session.execute.return_value = SimpleNamespace(rowcount=3)
        now = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)

        deleted = await cleanup_expired_plans(session, now=now)

        self.assertEqual(deleted, 3)
        session.commit.assert_awaited_once_with()
        session.rollback.assert_not_awaited()
        statement = session.execute.await_args.args[0]
        predicate = str(statement.whereclause)
        self.assertIn("requirement_plans.expires_at <", predicate)
        self.assertIn("requirement_plans.status !=", predicate)
        self.assertIn("requirement_plans.status =", predicate)
        self.assertIn("requirement_plans.updated_at <", predicate)


if __name__ == "__main__":
    unittest.main()
