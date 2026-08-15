"""Loki 日志证据适配器，供事故根因分析（RCA）使用。

请求使用 Loki ``query_range`` API，并对 service label 转义后构造 LogQL，只提取返回
stream 中的日志行。客户端设置连接 5 秒、总请求 15 秒和 2 次传输重试；JSON 响应及最终
交给 LLM 的证据文本都有独立上限。日志里只记录服务名和错误类型/HTTP 状态，不记录可能
包含凭据的完整异常或上游响应。

local 且未配置/查询失败时会返回明显可识别的 ``_MOCK_LOGS``，方便演示 RCA；非 local
环境绝不伪造日志，而是返回“未配置/查询失败”的降级标记，后续结论会据此降低置信度并
提示人工复核。这一 fail-closed 语义是生产安全边界，不要改成静默 mock。

用法示例::

    evidence = await LogClient().query_logs("checkout", limit=100)
    # evidence 是有界文本，可能是真实日志、无日志提示或明确的降级提示。
"""

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.integrations.http_utils import read_bounded_json

logger = get_logger("integrations.logs")

_MOCK_LOGS = """\
[ERROR] java.lang.OutOfMemoryError: Java heap space
[WARN]  GC overhead limit exceeded, pause 2.3s
[ERROR] Connection pool exhausted: active=50 max=50 waiting=120
[ERROR] Liveness probe failed: HTTP 500 from /health
[INFO]  Pod restarting due to OOMKilled (exit code 137)"""


class LogClient:
    """面向 RCA 的 Loki 日志查询客户端。

    ``base_url`` 主要供测试或多数据源调用覆盖；默认从 Settings 获取。实例只保存去除尾部
    斜杠后的地址，不保存请求内容或响应证据。
    """

    def __init__(self, base_url: str | None = None) -> None:
        """使用显式地址或 Settings 中的 Loki URL 初始化客户端。"""
        self._base = (base_url or get_settings().loki_url).rstrip("/")

    async def query_logs(self, service: str, limit: int = 50) -> str:
        """获取某服务近期错误日志，返回适合放入 LLM 上下文的有界文本。

        ``limit`` 被强制收敛到 1..500。任何网络、HTTP、JSON 或响应形状异常都被转换为
        环境相关的降级结果，因为一次证据源故障不应让整个 RCA 任务崩溃；但生产结果不会
        假装成功，调用方可通过 :func:`app.agents.incident_rca.graph._degraded_sources`
        识别提示文本。
        """
        settings = get_settings()
        if not self._base:
            if settings.is_local:
                logger.info("logs_mock", service=service)
                return _MOCK_LOGS
            logger.warning("logs_unconfigured", service=service)
            return "（日志源未配置，无法获取真实日志）"

        safe_service = service.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")
        query = f'{{service="{safe_service}"}} |= "ERROR"'
        limit = max(1, min(int(limit), 500))
        try:
            transport = httpx.AsyncHTTPTransport(retries=2)
            timeout = httpx.Timeout(15, connect=5, pool=5)
            async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
                async with client.stream(
                    "GET",
                    f"{self._base}/loki/api/v1/query_range",
                    params={"query": query, "limit": limit},
                ) as resp:
                    resp.raise_for_status()
                    data = await read_bounded_json(resp, settings.upstream_max_response_bytes)
            # 只抽取 line，不把 Loki stream labels 等高基数字段无界地送入模型。
            lines: list[str] = []
            if not isinstance(data, dict):
                raise ValueError("Loki response must be an object")
            for stream in data.get("data", {}).get("result", []):
                if not isinstance(stream, dict):
                    continue
                for _ts, line in stream.get("values", []):
                    if not isinstance(line, str):
                        continue
                    lines.append(line)
            evidence = "\n".join(lines[:limit])[: settings.rca_max_evidence_chars]
            return evidence or "（无错误日志）"
        except Exception as exc:  # noqa: BLE001 - RCA 降级但不伪造生产证据
            details: dict[str, object] = {"error_type": type(exc).__name__}
            if isinstance(exc, httpx.HTTPStatusError):
                details["status_code"] = exc.response.status_code
            logger.warning("logs_query_failed", **details)
            if settings.is_local:
                return _MOCK_LOGS
            return "（日志源查询失败，本次分析没有可用日志证据）"
