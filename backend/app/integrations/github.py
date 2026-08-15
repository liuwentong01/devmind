"""GitHub REST API 集成：拉取 PR diff、读取提交并回写 Review。

客户端默认从 Settings 读取 GitHub API base URL 和 PAT，以 Bearer 方式认证。它是代码
审查 Agent 与 ARQ worker 之间的薄适配层：请求/响应结构在这里校验，任务幂等与 PR
生命周期判断由 worker 协同完成。

可靠性边界：JSON 响应按配置限制解压后大小，diff 固定限制为 2 MiB；连接阶段 5 秒、
总请求 30 秒，传输层最多重试 2 次。评论幂等通过不可见 ``marker`` 搜索既有 review，
而 worker 在 LLM 调用前后各检查一次 head SHA，避免把旧 diff 的结论写到新提交。
日志仅记录 owner/repo/PR 编号，永远不记录 PAT、diff 或 review 正文。

用法示例::

    github = GitHubClient()
    sha = await github.get_pull_head_sha("acme", "payments", 42)
    diff = await github.get_pull_diff("acme", "payments", 42)
    marker = f"<!-- devmind-review:{sha} -->"
    created = await github.post_review_once(
        "acme", "payments", 42, f"审查结果\n{marker}", marker=marker
    )

``created`` 为 ``False`` 表示 marker 已存在，并非请求失败。
"""

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.integrations.http_utils import read_bounded_json

logger = get_logger("integrations.github")
MAX_DIFF_BYTES = 2 * 1024 * 1024


class GitHubClient:
    """轻量、无状态的 GitHub REST 客户端（基于 PAT）。

    测试可传入 ``token`` 和 ``api_base`` 覆盖 Settings；生产代码应使用默认构造，确保
    凭据只来自受控配置。每个公开调用创建并关闭独立 AsyncClient，避免连接泄漏。
    """

    def __init__(self, token: str | None = None, api_base: str | None = None) -> None:
        """初始化 API 地址、可选 PAT 与 JSON 响应大小上限。"""
        settings = get_settings()
        self._token = token or settings.github_token
        self._base = (api_base or settings.github_api_base).rstrip("/")
        self._max_json_bytes = settings.upstream_max_response_bytes

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        """构造版本固定的请求头；没有 token 时不发送空 Authorization。"""
        headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _client(self) -> httpx.AsyncClient:
        """创建带有限重试和分阶段超时的短生命周期 HTTP 客户端。"""
        transport = httpx.AsyncHTTPTransport(retries=2)
        timeout = httpx.Timeout(30, connect=5, pool=5)
        return httpx.AsyncClient(timeout=timeout, transport=transport)

    async def get_pull_head_sha(self, owner: str, repo: str, number: int) -> str:
        """获取 PR 当前 head SHA，用于丢弃过期任务和生成幂等标识。

        响应必须是对象且包含非空 ``head.sha``；否则即便 HTTP 成功也 fail-closed，避免
        后续以空 SHA 生成冲突的 job id 或 review marker。
        """
        url = f"{self._base}/repos/{owner}/{repo}/pulls/{number}"
        async with self._client() as client:
            async with client.stream("GET", url, headers=self._headers()) as resp:
                resp.raise_for_status()
                data = await read_bounded_json(resp, self._max_json_bytes)
        if not isinstance(data, dict):
            raise ValueError("GitHub PR 响应格式错误")
        head = data.get("head")
        sha = head.get("sha") if isinstance(head, dict) else None
        if not isinstance(sha, str) or not sha:
            raise ValueError("GitHub PR 响应缺少 head SHA")
        return sha

    async def get_pull_diff(self, owner: str, repo: str, number: int) -> str:
        """获取某个 PR 的 unified diff 文本，最多允许 2 MiB。

        非 UTF-8 字节使用替换字符解码；这保留其余可审查内容，但不会因单个异常字节让
        整个异步任务失败。超过上限则拒绝部分结果，防止 LLM 审查不完整 diff。
        """
        url = f"{self._base}/repos/{owner}/{repo}/pulls/{number}"
        async with self._client() as client:
            async with client.stream(
                "GET", url, headers=self._headers("application/vnd.github.v3.diff")
            ) as resp:
                resp.raise_for_status()
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_DIFF_BYTES:
                        raise ValueError("PR diff 超过 2 MiB 安全上限")
        return bytes(body).decode("utf-8", errors="replace")

    async def list_recent_commits(self, owner: str, repo: str, limit: int = 10) -> list[dict]:
        """获取默认分支近期提交的有界摘要，用于 RCA 关联发布。

        只返回 SHA、首行消息、作者与日期，并再次截断各文本字段；不会把 GitHub 的完整
        用户或提交对象传入 LLM。调用方应从可信配置提供 owner/repo 和合理的 ``limit``。
        """
        url = f"{self._base}/repos/{owner}/{repo}/commits"
        async with self._client() as client:
            async with client.stream(
                "GET",
                url,
                headers=self._headers(),
                params={"per_page": limit},
            ) as resp:
                resp.raise_for_status()
                data = await read_bounded_json(resp, self._max_json_bytes)
        if not isinstance(data, list):
            raise ValueError("GitHub commits 响应格式错误")
        commits = []
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("GitHub commits 响应格式错误")
            commit = item.get("commit", {})
            if not isinstance(commit, dict):
                raise ValueError("GitHub commits 响应格式错误")
            author = commit.get("author")
            author = author if isinstance(author, dict) else {}
            commits.append(
                {
                    "sha": str(item.get("sha") or "")[:7],
                    "message": str(commit.get("message") or "").splitlines()[0][:1000],
                    "author": str(author.get("name") or "")[:200],
                    "date": str(author.get("date") or "")[:100],
                }
            )
        return commits

    async def post_review_once(
        self,
        owner: str,
        repo: str,
        number: int,
        body: str,
        *,
        marker: str,
    ) -> bool:
        """按隐藏 marker 幂等回写 Review；已存在时返回 ``False``。

        最多检查 10 页、每页 100 条 review，以限制上游调用量。搜索和创建并非 GitHub
        提供的原子操作，因此极端并发下仍是“至少一次 + 最佳努力去重”；稳定的 ARQ job
        id 是第一层防重，本方法是网络超时/任务重试后的第二层防重。
        """
        url = f"{self._base}/repos/{owner}/{repo}/pulls/{number}/reviews"
        async with self._client() as client:
            for page in range(1, 11):
                async with client.stream(
                    "GET",
                    url,
                    headers=self._headers(),
                    params={"per_page": 100, "page": page},
                ) as existing:
                    existing.raise_for_status()
                    reviews = await read_bounded_json(existing, self._max_json_bytes)
                if not isinstance(reviews, list):
                    raise ValueError("GitHub reviews 响应格式错误")
                for review in reviews:
                    if not isinstance(review, dict):
                        raise ValueError("GitHub reviews 响应格式错误")
                    if marker in str(review.get("body") or ""):
                        logger.info("github_review_reused", owner=owner, repo=repo, number=number)
                        return False
                if len(reviews) < 100:
                    break
            async with client.stream(
                "POST",
                url,
                headers=self._headers(),
                json={"event": "COMMENT", "body": body},
            ) as resp:
                resp.raise_for_status()
        logger.info("github_review_posted", owner=owner, repo=repo, number=number)
        return True

    async def post_review(self, owner: str, repo: str, number: int, body: str) -> None:
        """兼容旧调用方的非去重回写接口。

        新任务应优先使用 :meth:`post_review_once`；此方法每次都会创建评论，仅保留给确实
        需要重复发言的历史调用方。
        """
        url = f"{self._base}/repos/{owner}/{repo}/pulls/{number}/reviews"
        async with self._client() as client:
            async with client.stream(
                "POST",
                url,
                headers=self._headers(),
                json={"event": "COMMENT", "body": body},
            ) as resp:
                resp.raise_for_status()
        logger.info("github_review_posted", owner=owner, repo=repo, number=number)
