"""HTTP/API 的共享安全原语与 ASGI 中间件。

调用链：``app.main`` 把请求体限制和安全响应头中间件包在所有路由外，并通过 FastAPI
``Depends(require_api_key)`` 保护普通/管理 API；Webhook 路由复用 HMAC/Bearer 校验，代码
索引路由复用真实路径授权。curl 示例：
``curl -H 'X-API-Key: <普通密钥>' http://localhost:8000/api/admin/cost``（普通密钥会得到
403，管理密钥才可访问）。

生产边界：API key 是服务级最小鉴权，不提供终端用户身份、租户 RBAC 或不可抵赖审计；生产
入口仍需 OIDC/IAP、速率限制、TLS 与网络策略。密钥只从服务端配置读取，禁止写入浏览器包、
URL 或日志。中间件限制单请求风险，但不是 WAF，也不能代替代理层的连接/并发限制。
"""

from __future__ import annotations

import asyncio
import hmac
import re
import time
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import bound_contextvars

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

API_KEY_HEADER_NAME = "X-API-Key"
_api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)

_GITHUB_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_SINGLETON_SECURITY_HEADERS = frozenset(
    {
        b"authorization",
        b"content-length",
        b"content-type",
        b"host",
        b"transfer-encoding",
        b"x-api-key",
        b"x-github-delivery",
        b"x-github-event",
        b"x-hub-signature-256",
    }
)


def security_response_headers(
    request_id: str, *, strict_transport_security: bool = False
) -> dict[str, str]:
    """构造所有响应（包括全局 500）必须携带的 request id 与防护头。

    非本地额外启用 HSTS/CSP；API 响应强制 ``no-store``，避免代理或浏览器缓存含内部数据的
    JSON/SSE 错误。若前端由独立 Nginx 托管，应由该入口设置适合静态资源的 CSP/缓存策略。
    """
    headers = {
        "X-Request-ID": request_id,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Cache-Control": "no-store",
    }
    if strict_transport_security:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        )
    return headers


def _matches_secret(candidate: str | None, expected_values: list[str]) -> bool:
    """恒定时间比较候选密钥，并遍历全部轮换密钥避免暴露命中位置。"""
    if not candidate:
        return False
    matched = False
    for expected in expected_values:
        matched |= hmac.compare_digest(candidate, expected)
    return matched


def _unauthorized() -> HTTPException:
    """构造统一且不泄漏“密钥不存在/错误”差异的 401 响应。"""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="身份验证失败",
        headers={"WWW-Authenticate": 'ApiKey realm="devmind"'},
    )


async def require_api_key(
    api_key: Annotated[str | None, Security(_api_key_header)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """FastAPI 依赖：校验普通 API key；管理 key 同时具有普通 API 权限。

    本地配置关闭鉴权时直接通过；非本地是否允许关闭由 ``Settings`` 启动校验阻止。
    """
    if not settings.api_auth_enabled:
        return
    allowed = settings.api_key_list + settings.admin_api_key_list
    if not _matches_secret(api_key, allowed):
        raise _unauthorized()


async def require_admin_api_key(
    api_key: Annotated[str | None, Security(_api_key_header)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """FastAPI 依赖：仅接受管理 key，并区分“未认证”401 与“权限不足”403。"""
    if not settings.api_auth_enabled:
        return
    if _matches_secret(api_key, settings.admin_api_key_list):
        return
    if _matches_secret(api_key, settings.api_key_list):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    raise _unauthorized()


def verify_hmac_sha256(body: bytes, signature: str | None, secret: str) -> bool:
    """校验形如 ``sha256=<hex>`` 的 HMAC-SHA256 签名。

    必须传入尚未 JSON 解码/重编码的原始请求字节；任何空白或键顺序变化都会改变摘要。
    """
    if not secret or not signature or len(signature) != 71:
        return False
    prefix, separator, digest = signature.partition("=")
    if (
        separator != "="
        or prefix.lower() != "sha256"
        or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)
    ):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, "sha256").hexdigest()
    return hmac.compare_digest(expected, digest.lower())


def verify_bearer_secret(authorization: str | None, secret: str) -> bool:
    """以恒定时间比较校验 ``Authorization: Bearer <secret>``。"""
    if not secret or not authorization:
        return False
    scheme, separator, credential = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not credential:
        return False
    return hmac.compare_digest(credential, secret)


def validate_github_repository(owner: str, repo: str) -> bool:
    """GitHub owner/repo 只能是单个 URL path segment，阻断路径注入。"""
    return bool(_GITHUB_NAME_RE.fullmatch(owner) and _GITHUB_NAME_RE.fullmatch(repo))


class PathAccessError(ValueError):
    """索引路径不存在、被禁用或超出允许根目录。"""


def resolve_code_index_path(raw_path: str, settings: Settings) -> Path:
    """解析并授权代码索引目录，防止任意文件读取与符号链接逃逸。

    示例：生产配置 ``CODE_INDEX_ALLOWED_ROOTS=/srv/repos`` 时，``/srv/repos/api`` 可通过，
    ``/etc`` 或指向根目录外的 symlink 会被拒绝。返回值是 canonical ``Path``，调用方必须
    使用它而不是重新使用未经校验的 ``raw_path``。
    """
    try:
        resolved = Path(raw_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PathAccessError("索引路径不存在或不可访问") from exc
    if not resolved.is_dir():
        raise PathAccessError("索引路径不存在或不是目录")

    configured_roots = settings.code_index_allowed_root_list
    if not configured_roots:
        if settings.is_local:
            return resolved
        raise PathAccessError("代码索引功能未配置允许目录")

    allowed_roots: list[Path] = []
    for raw_root in configured_roots:
        try:
            root = Path(raw_root).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise PathAccessError("代码索引允许目录配置无效") from exc
        if not root.is_dir():
            raise PathAccessError("代码索引允许目录配置无效")
        allowed_roots.append(root)

    # 先 resolve 再判断父子关系，字符串前缀比较会把 /srv/repos-evil 误判为 /srv/repos 子目录，
    # 未解析的符号链接也可能在校验后跳出根目录。
    if not any(resolved == root or resolved.is_relative_to(root) for root in allowed_roots):
        raise PathAccessError("索引路径不在允许目录内")
    return resolved


class RequestBodyTooLarge(Exception):
    """请求流在没有可信 Content-Length 时超过上限。"""


class RequestBodyLimitMiddleware:
    """同时限制声明长度和实际 ASGI 请求流，避免 chunked/头部歧义绕过。

    代理可能移除或不信任 ``Content-Length``，所以只检查 header 不够；这里包装 ``receive``
    对实际分片累计计数。重复的认证/长度/Host 等 singleton header 会被拒绝，以免前置代理与
    应用采用不同解析结果而形成 request smuggling。WebSocket 等非 HTTP scope 原样透传。
    """

    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        """保存下游 ASGI 应用与实际请求体硬上限。"""
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """校验 HTTP 消息边界并用计数包装后的 ``receive`` 调用下游应用。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # RFC 对部分 header 允许重复，但安全关键 singleton 一旦重复，各层“取首个还是末个”的
        # 差异会造成鉴权或消息边界混淆，最安全的处理是直接 400。
        header_counts: dict[bytes, int] = {}
        for name, _value in scope.get("headers", []):
            normalized = name.lower()
            if normalized in _SINGLETON_SECURITY_HEADERS:
                header_counts[normalized] = header_counts.get(normalized, 0) + 1
        if any(count > 1 for count in header_counts.values()):
            await self._reject(scope, receive, send, status.HTTP_400_BAD_REQUEST, "请求头无效")
            return

        raw_lengths = [
            value for name, value in scope.get("headers", []) if name.lower() == b"content-length"
        ]
        has_transfer_encoding = any(
            name.lower() == b"transfer-encoding" for name, _value in scope.get("headers", [])
        )
        if raw_lengths and has_transfer_encoding:
            await self._reject(scope, receive, send, status.HTTP_400_BAD_REQUEST, "请求头无效")
            return
        if len(raw_lengths) > 1:
            await self._reject(scope, receive, send, status.HTTP_400_BAD_REQUEST, "请求头无效")
            return
        if raw_lengths:
            try:
                if not re.fullmatch(rb"[0-9]+", raw_lengths[0]):
                    raise ValueError
                declared_length = int(raw_lengths[0])
            except (TypeError, ValueError):
                await self._reject(scope, receive, send, status.HTTP_400_BAD_REQUEST, "请求头无效")
                return
            if declared_length < 0:
                await self._reject(scope, receive, send, status.HTTP_400_BAD_REQUEST, "请求头无效")
                return
            if declared_length > self.max_body_bytes:
                await self._reject(
                    scope,
                    receive,
                    send,
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    "请求体过大",
                )
                return

        received = 0

        async def limited_receive() -> Message:
            """按 ASGI 分片累计真实字节数；不依赖可伪造或缺失的 Content-Length。"""
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await self._reject(
                scope,
                receive,
                send,
                status.HTTP_413_CONTENT_TOO_LARGE,
                "请求体过大",
            )

    @staticmethod
    async def _reject(
        scope: Scope,
        receive: Receive,
        send: Send,
        status_code: int,
        detail: str,
    ) -> None:
        """在路由执行前返回稳定 JSON 错误；消费/关闭请求流由 ASGI 服务器负责。"""
        response = JSONResponse({"detail": detail}, status_code=status_code)
        await response(scope, receive, send)


class SecurityHeadersMiddleware:
    """为请求绑定 request id、补全安全头并输出脱敏结构化访问日志。

    request id 写入 ASGI state，异常处理器可把它回给调用方；contextvar 又让同一异步调用链
    的业务日志自动关联。访问日志只含 method/path/status/duration，不含 query、header 或
    body。SSE 的耗时表示连接完整生命周期，而非首字节延迟。
    """

    def __init__(self, app: ASGIApp, *, strict_transport_security: bool = False) -> None:
        """保存下游应用，并决定是否为非本地入口追加 HSTS/CSP。"""
        self.app = app
        self.strict_transport_security = strict_transport_security

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """为一次 HTTP 请求绑定上下文，并在响应开始时补安全头。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started_at = time.perf_counter()
        status_code = 500

        async def send_with_headers(message: Message) -> None:
            """拦截 response.start，在字节发送前合并必需响应头并记录最终状态码。"""
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = MutableHeaders(scope=message)
                required_headers = security_response_headers(
                    request_id,
                    strict_transport_security=self.strict_transport_security,
                )
                # 业务路由可能设置 no-cache（如 SSE）；保留其语义同时附加 no-store，避免敏感
                # 流在浏览器/代理落盘。MutableHeaders 赋值也确保不会产生冲突的重复安全头。
                cache_control = headers.get("cache-control")
                if cache_control and "no-store" not in cache_control.lower():
                    required_headers["Cache-Control"] = f"{cache_control}, no-store"
                for name, value in required_headers.items():
                    headers[name] = value
            await send(message)

        # contextvars 会传播到同一请求的异步任务，并在结束后恢复，避免串号。
        with bound_contextvars(request_id=request_id):
            outcome = "completed"
            try:
                await self.app(scope, receive, send_with_headers)
            except BaseException as exc:
                outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
                raise
            finally:
                path = str(scope.get("path", ""))[:2048]
                if path not in {"/api/health", "/api/health/live"}:
                    get_logger("http").info(
                        "http_request_complete",
                        method=scope.get("method", ""),
                        path=path,
                        status_code=status_code,
                        duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
                        outcome=outcome,
                    )
