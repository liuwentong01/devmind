"""Jira 集成：创建 Issue（未配置时 dry-run）。"""

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("integrations.jira")

# 任务类型 → Jira issuetype 名称
ISSUETYPE_MAP = {"backend": "Task", "frontend": "Task", "task": "Task"}


class JiraClient:
    """Jira Cloud REST v3 客户端。未配置 Atlassian 时进入 dry-run。"""

    def __init__(self) -> None:
        s = get_settings()
        self._base = s.atlassian_base_url.rstrip("/")
        self._auth = (s.atlassian_email, s.atlassian_api_token)
        self._configured = bool(self._base and s.atlassian_email and s.atlassian_api_token)

    @property
    def configured(self) -> bool:
        return self._configured

    def _adf(self, text: str) -> dict:
        """把纯文本包装成 Jira 的 Atlassian Document Format。"""
        return {
            "type": "doc",
            "version": 1,
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": text or " "}]}
            ],
        }

    async def create_issue(
        self,
        project: str,
        summary: str,
        description: str,
        issue_type: str = "Task",
        parent_key: str | None = None,
    ) -> str:
        """创建一个 Issue，返回 key。未配置时返回 dry-run key。"""
        if not self._configured:
            fake = f"DRY-{abs(hash(summary)) % 10000}"
            logger.info("jira_dry_run", summary=summary, key=fake)
            return fake

        fields: dict = {
            "project": {"key": project},
            "summary": summary[:250],
            "description": self._adf(description),
            "issuetype": {"name": issue_type},
        }
        if parent_key:
            fields["parent"] = {"key": parent_key}

        url = f"{self._base}/rest/api/3/issue"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, auth=self._auth, json={"fields": fields})
            resp.raise_for_status()
            data = resp.json()
        key = data.get("key", "")
        logger.info("jira_issue_created", key=key, summary=summary)
        return key
