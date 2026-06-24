"""监控指标集成：查询 Prometheus；未配置时返回 mock 数据。

返回适合直接喂给 LLM 的文本摘要。
"""

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("integrations.monitoring")

_MOCK_METRICS = (
    "container_memory_usage{{service={service}}}: 最近 30min 从 60% 持续升至 98%（接近 limit）\n"
    "http_request_duration_p99{{service={service}}}: 从 220ms 升至 4.8s\n"
    "http_5xx_rate{{service={service}}}: 从 0.1% 升至 12%\n"
    "pod_restart_total{{service={service}}}: 最近 15min 重启 3 次"
)


class MonitoringClient:
    """Prometheus 指标查询客户端。"""

    def __init__(self, base_url: str | None = None) -> None:
        self._base = (base_url or get_settings().prometheus_url).rstrip("/")

    async def query_metrics(self, service: str) -> str:
        """获取某服务的关键指标摘要。未配置 Prometheus 时返回 mock。"""
        if not self._base:
            logger.info("monitoring_mock", service=service)
            return _MOCK_METRICS.format(service=service or "unknown")

        # 真实环境：查询若干关键指标（这里用一个示例 PromQL）
        promql = f'rate(http_requests_total{{service="{service}"}}[5m])'
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self._base}/api/v1/query", params={"query": promql}
                )
                resp.raise_for_status()
                data = resp.json()
            return f"PromQL `{promql}` 结果：{data.get('data', {}).get('result', [])}"
        except Exception as exc:  # noqa: BLE001 - 拉取失败降级为 mock，不阻断分析
            logger.warning("monitoring_query_failed", error=str(exc))
            return _MOCK_METRICS.format(service=service or "unknown")
