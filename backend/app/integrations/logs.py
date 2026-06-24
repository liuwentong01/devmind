"""日志集成：查询 Loki；未配置时返回 mock 日志。"""

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("integrations.logs")

_MOCK_LOGS = """\
[ERROR] java.lang.OutOfMemoryError: Java heap space
[WARN]  GC overhead limit exceeded, pause 2.3s
[ERROR] Connection pool exhausted: active=50 max=50 waiting=120
[ERROR] Liveness probe failed: HTTP 500 from /health
[INFO]  Pod restarting due to OOMKilled (exit code 137)"""


class LogClient:
    """Loki 日志查询客户端。"""

    def __init__(self, base_url: str | None = None) -> None:
        self._base = (base_url or get_settings().loki_url).rstrip("/")

    async def query_logs(self, service: str, limit: int = 50) -> str:
        """获取某服务最近的错误日志。未配置 Loki 时返回 mock。"""
        if not self._base:
            logger.info("logs_mock", service=service)
            return _MOCK_LOGS

        query = f'{{service="{service}"}} |= "ERROR"'
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self._base}/loki/api/v1/query_range",
                    params={"query": query, "limit": limit},
                )
                resp.raise_for_status()
                data = resp.json()
            # 简化：拼接返回的日志行
            lines: list[str] = []
            for stream in data.get("data", {}).get("result", []):
                for _ts, line in stream.get("values", []):
                    lines.append(line)
            return "\n".join(lines[:limit]) or "（无错误日志）"
        except Exception as exc:  # noqa: BLE001 - 降级为 mock
            logger.warning("logs_query_failed", error=str(exc))
            return _MOCK_LOGS
