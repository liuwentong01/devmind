"""Confluence 集成：读取 PRD 页面正文。"""

import re

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("integrations.confluence")

_TAG_RE = re.compile(r"<[^>]+>")


def extract_page_id(url_or_id: str) -> str:
    """从 Confluence URL 或纯 id 中提取 pageId。"""
    if url_or_id.isdigit():
        return url_or_id
    # 形如 .../pages/123456/Title 或 ?pageId=123456
    m = re.search(r"/pages/(\d+)", url_or_id) or re.search(r"pageId=(\d+)", url_or_id)
    if m:
        return m.group(1)
    raise ValueError("无法从链接解析 Confluence pageId，请直接粘贴 PRD 文本")


class ConfluenceClient:
    """Confluence Cloud REST 客户端。"""

    def __init__(self) -> None:
        s = get_settings()
        self._base = s.atlassian_base_url.rstrip("/")
        self._auth = (s.atlassian_email, s.atlassian_api_token)
        self._configured = bool(self._base and s.atlassian_email and s.atlassian_api_token)

    @property
    def configured(self) -> bool:
        return self._configured

    async def get_page_text(self, url_or_id: str) -> str:
        """获取页面正文纯文本（storage HTML 去标签）。"""
        if not self._configured:
            raise ValueError("未配置 Atlassian，无法读取 Confluence，请直接粘贴 PRD 文本")
        page_id = extract_page_id(url_or_id)
        url = f"{self._base}/wiki/rest/api/content/{page_id}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                url, auth=self._auth, params={"expand": "body.storage"}
            )
            resp.raise_for_status()
            data = resp.json()
        html = data.get("body", {}).get("storage", {}).get("value", "")
        text = _TAG_RE.sub("", html)
        logger.info("confluence_page_loaded", page_id=page_id, length=len(text))
        return text
