"""Confluence Cloud 页面读取适配器。

输入可以是纯数字 ``pageId``，也可以是包含 ``/pages/<id>`` 或 ``?pageId=<id>`` 的
Confluence URL。客户端使用 Atlassian 邮箱/API Token 的 HTTP Basic Auth 请求 REST API，
展开 ``body.storage`` 后把 HTML 转成适合需求分析 Agent 使用的纯文本。

这里刻意采用流式读取并限制“解压后的”响应字节数：攻击者或异常上游可能省略/伪造
``Content-Length``，仅检查响应头仍可能造成内存耗尽。正文另有字符数上限，防止巨型
页面直接进入 LLM 上下文。日志只记录 page id 和结果长度，不记录正文或凭据。

非 local 环境未配置凭据时会直接报错（fail-closed），不会返回示例 PRD。调用示例::

    client = ConfluenceClient()
    if client.configured:
        text = await client.get_page_text(
            "https://acme.atlassian.net/wiki/spaces/DEV/pages/123456/Checkout"
        )

可能抛出 :class:`ConfluencePageIdError`、:class:`ConfluenceResponseTooLarge`、
``httpx.HTTPStatusError`` 或不可信响应格式对应的 ``RuntimeError``。
"""

import html as html_lib
import json
import re

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("integrations.confluence")

_TAG_RE = re.compile(r"<[^>]+>")


class ConfluencePageIdError(ValueError):
    """用户提供的 Confluence 页面标识无效。"""


class ConfluenceResponseTooLarge(RuntimeError):
    """Confluence 原始响应或提取正文超过配置上限。"""


def extract_page_id(url_or_id: str) -> str:
    """从 Confluence URL 或纯数字中提取 ``pageId``。

    只接受数字 ID，不把任意路径片段拼进上游 URL，从源头避免路径注入。例如
    ``extract_page_id(".../pages/123/Title")`` 返回 ``"123"``；无法识别时抛出
    :class:`ConfluencePageIdError`。
    """
    if url_or_id.isdigit():
        return url_or_id
    # 形如 .../pages/123456/Title 或 ?pageId=123456
    m = re.search(r"/pages/(\d+)", url_or_id) or re.search(r"pageId=(\d+)", url_or_id)
    if m:
        return m.group(1)
    raise ConfluencePageIdError("无法从链接解析 Confluence pageId")


class ConfluenceClient:
    """最小化的 Confluence Cloud REST 客户端。

    实例化时读取并快照配置，使一次调用中的目标地址、认证与资源上限保持一致。
    ``configured`` 仅表示 base URL、邮箱和 token 同时存在，并不代表凭据已被上游验证。
    """

    def __init__(self) -> None:
        """从 Settings 快照连接、Basic Auth 与两级响应容量上限。"""
        s = get_settings()
        self._base = s.atlassian_base_url.rstrip("/")
        self._auth = (s.atlassian_email, s.atlassian_api_token)
        self._configured = bool(self._base and s.atlassian_email and s.atlassian_api_token)
        self._max_response_bytes = s.confluence_max_response_bytes
        self._max_text_chars = s.confluence_max_text_chars

    @property
    def configured(self) -> bool:
        """返回 Atlassian 三项连接配置是否完整。"""
        return self._configured

    async def get_page_text(self, url_or_id: str) -> str:
        """读取页面 ``storage`` HTML，并返回有界的纯文本。

        请求超时为 30 秒（连接阶段 10 秒），HTTP 非 2xx 由 httpx 原样转换为
        ``HTTPStatusError``。当前方法不做自动业务重试，调用它的任务可依据自身幂等语义
        决定是否重试。即便服务端提供了 ``Content-Length``，仍会逐块累计实际字节数。
        """
        if not self._configured:
            raise RuntimeError("Confluence integration is not configured")
        page_id = extract_page_id(url_or_id)
        url = f"{self._base}/wiki/rest/api/content/{page_id}"
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "GET",
                url,
                auth=self._auth,
                params={"expand": "body.storage"},
            ) as resp:
                resp.raise_for_status()
                content_length = resp.headers.get("content-length")
                if content_length and content_length.isdigit():
                    if int(content_length) > self._max_response_bytes:
                        raise ConfluenceResponseTooLarge("Confluence response is too large")

                raw = bytearray()
                async for chunk in resp.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > self._max_response_bytes:
                        raise ConfluenceResponseTooLarge("Confluence response is too large")

        try:
            # json.loads 接受 bytes-like 对象；保持 bytearray 到这里可以避免多一次大对象复制。
            data = json.loads(raw)
            storage_html = data.get("body", {}).get("storage", {}).get("value", "")
        except (AttributeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError("Confluence returned an invalid response") from exc
        if not isinstance(storage_html, str):
            raise RuntimeError("Confluence returned an invalid response")

        # storage 格式是 HTML；去标签后反转义 &amp; 等实体，避免把编码文本误传给 LLM。
        text = html_lib.unescape(_TAG_RE.sub("", storage_html)).strip()
        if len(text) > self._max_text_chars:
            raise ConfluenceResponseTooLarge("Confluence page text is too large")
        logger.info("confluence_page_loaded", page_id=page_id, length=len(text))
        return text
