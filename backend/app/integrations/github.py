"""GitHub 集成：拉取 PR diff、回写 Review 评论。

封装成「工具」，供代码审查 Agent 与 ARQ worker 调用。
后续可改造成 MCP Server 形式注册进工具注册中心。
"""

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("integrations.github")


class GitHubClient:
    """轻量 GitHub REST 客户端（基于 PAT）。"""

    def __init__(self, token: str | None = None, api_base: str | None = None) -> None:
        settings = get_settings()
        self._token = token or settings.github_token
        self._base = (api_base or settings.github_api_base).rstrip("/")

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def get_pull_diff(self, owner: str, repo: str, number: int) -> str:
        """获取某个 PR 的统一 diff 文本。"""
        url = f"{self._base}/repos/{owner}/{repo}/pulls/{number}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=self._headers("application/vnd.github.v3.diff"))
            resp.raise_for_status()
            return resp.text

    async def post_review(self, owner: str, repo: str, number: int, body: str) -> None:
        """以一条整体 Review 评论的形式回写到 PR。"""
        url = f"{self._base}/repos/{owner}/{repo}/pulls/{number}/reviews"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url, headers=self._headers(), json={"event": "COMMENT", "body": body}
            )
            resp.raise_for_status()
        logger.info("github_review_posted", owner=owner, repo=repo, number=number)
