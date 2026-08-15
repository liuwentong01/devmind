"""Jira Cloud REST v3 集成：按稳定标签幂等创建 Issue。

认证使用与 Confluence 相同的 Atlassian 邮箱/API Token（HTTP Basic Auth）。构造客户端
时要求 base URL、邮箱和 token 要么全部提供、要么全部留空，避免“看似已配置但请求必然
失败”的半配置状态。真实请求具有连接/总超时和有限传输重试；所有 JSON 响应都按解压后
字节数限制并校验结构。

local 环境可显式允许 dry-run，此时不会请求 Jira，而是根据幂等标签或标题生成稳定的
``DRY-XXXXXXXX`` key。生产配置校验禁止 dry-run，因此非 local 环境缺少 Jira 配置时
必须 fail-closed。日志只记录 issue key/稳定标签，不记录标题、描述或凭据。

用法示例::

    jira = JiraClient()
    key = await jira.create_issue(
        project="PAY",
        summary="实现退款接口",
        description="验收标准：重复请求不会重复退款",
        issue_type="Task",
        idempotency_label="devmind-plan-abc-task-001",
    )

同一项目和 label 已存在时返回原 key；没有 label 时每次调用都可能创建新 Issue。
"""

import hashlib

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.integrations.http_utils import read_bounded_json

logger = get_logger("integrations.jira")

# 任务类型 → Jira issuetype 名称
ISSUETYPE_MAP = {"backend": "Task", "frontend": "Task", "task": "Task"}


class JiraConfigurationError(RuntimeError):
    """Jira credentials or the target project are not configured safely."""


class JiraClient:
    """Jira Cloud REST v3 客户端。

    未配置时只有在 ``jira_dry_run_allowed`` 为真才进入 dry-run。``configured`` 与
    ``dry_run_allowed`` 属性可用于 UI 展示，但调用方仍应让 :meth:`create_issue` 执行
    最终防线校验。
    """

    def __init__(self) -> None:
        """读取并验证 Atlassian 配置组，同时快照 dry-run/响应上限。"""
        s = get_settings()
        self._base = s.atlassian_base_url.rstrip("/")
        self._auth = (s.atlassian_email, s.atlassian_api_token)
        parts = (self._base, s.atlassian_email, s.atlassian_api_token)
        if any(parts) and not all(parts):
            raise JiraConfigurationError(
                "Atlassian base URL, email and API token must be configured together"
            )
        self._configured = bool(all(parts))
        self._dry_run_allowed = s.jira_dry_run_allowed
        self._max_json_bytes = s.upstream_max_response_bytes

    @property
    def configured(self) -> bool:
        """返回 Atlassian base URL、邮箱和 token 是否同时存在。"""
        return self._configured

    @property
    def dry_run_allowed(self) -> bool:
        """返回当前环境是否明确允许只生成模拟 key。"""
        return self._dry_run_allowed

    def _adf(self, text: str) -> dict:
        """把纯文本包装成 Jira 所需的 Atlassian Document Format（ADF）。

        空文本替换为一个空格，确保生成的 paragraph 仍满足 Jira schema。这里不接受调用方
        提供任意 ADF，避免把未经验证的复杂节点直接透传给上游。
        """
        return {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": text or " "}]}],
        }

    def _client(self) -> httpx.AsyncClient:
        """创建携带 Basic Auth、有限重试及分阶段超时的 HTTP 客户端。"""
        transport = httpx.AsyncHTTPTransport(retries=2)
        timeout = httpx.Timeout(30, connect=5, pool=5)
        return httpx.AsyncClient(timeout=timeout, transport=transport, auth=self._auth)

    async def _find_by_label(self, project: str, label: str) -> str | None:
        """按内部稳定标签查找已创建 Issue，供超时/任务重试去重。

        project/label 在拼入 JQL 前转义反斜杠和双引号，搜索最多返回一个最新结果。若上游
        返回形状异常则拒绝继续创建，避免查询解析失败后误判为“不存在”并制造重复 Issue。
        """
        safe_project = project.replace("\\", "\\\\").replace('"', '\\"')
        safe_label = label.replace("\\", "\\\\").replace('"', '\\"')
        jql = f'project = "{safe_project}" AND labels = "{safe_label}" ORDER BY created DESC'
        async with self._client() as client:
            async with client.stream(
                "GET",
                f"{self._base}/rest/api/3/search/jql",
                params={"jql": jql, "fields": "key", "maxResults": 1},
            ) as resp:
                resp.raise_for_status()
                payload = await read_bounded_json(resp, self._max_json_bytes)
            if not isinstance(payload, dict) or not isinstance(payload.get("issues", []), list):
                raise ValueError("Jira 搜索响应格式错误")
            issues = payload.get("issues", [])
        if issues:
            first = issues[0]
            if not isinstance(first, dict):
                raise ValueError("Jira 搜索响应格式错误")
            return str(first.get("key") or "") or None
        return None

    async def create_issue(
        self,
        project: str,
        summary: str,
        description: str,
        issue_type: str = "Task",
        parent_key: str | None = None,
        idempotency_label: str | None = None,
    ) -> str:
        """创建一个 Issue 并返回 key；相同标签已存在时直接复用。

        ``summary`` 在发送前截断为 Jira 常见的 250 字符上限，description 转为 ADF；
        ``parent_key`` 用于子任务/层级关系。HTTP 传输重试可能发生在响应丢失之后，因此
        生产批量创建必须提供稳定 ``idempotency_label``，由“先查后建”降低重复概率。
        Jira 没有在此流程中提供原子 create-if-absent，极端并发仍需调用方租约约束。
        """
        if not self._configured and not self._dry_run_allowed:
            raise RuntimeError("Jira integration is not configured")
        if not self._configured:
            seed = idempotency_label or summary
            fake = f"DRY-{hashlib.sha256(seed.encode()).hexdigest()[:8].upper()}"
            logger.info("jira_dry_run", key=fake)
            return fake

        if idempotency_label:
            existing = await self._find_by_label(project, idempotency_label)
            if existing:
                logger.info("jira_issue_reused", key=existing, label=idempotency_label)
                return existing

        fields: dict = {
            "project": {"key": project},
            "summary": summary[:250],
            "description": self._adf(description),
            "issuetype": {"name": issue_type},
        }
        if parent_key:
            fields["parent"] = {"key": parent_key}
        if idempotency_label:
            fields["labels"] = [idempotency_label]

        url = f"{self._base}/rest/api/3/issue"
        async with self._client() as client:
            async with client.stream("POST", url, json={"fields": fields}) as resp:
                resp.raise_for_status()
                data = await read_bounded_json(resp, self._max_json_bytes)
        if not isinstance(data, dict):
            raise ValueError("Jira 创建响应格式错误")
        key = data.get("key", "")
        if not isinstance(key, str) or not key:
            raise ValueError("Jira 创建响应缺少 issue key")
        logger.info("jira_issue_created", key=key)
        return key
