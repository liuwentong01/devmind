"""Prometheus 指标证据适配器，返回适合 RCA 使用的文本摘要。

当前实现查询一个受控模板生成的 PromQL，并把 Prometheus result 序列化成有界文本。
service label 会转义反斜杠、引号并移除换行，避免用户输入改变查询结构。请求具有连接
5 秒、总请求 15 秒、2 次传输重试以及解压后响应大小限制；最终证据还会按 RCA 字符上限
截断，防止时序数量异常挤满 LLM 上下文。

local 环境可以返回 ``_MOCK_METRICS`` 支持演示。非 local 环境未配置或查询失败时仅返回
明确的降级提示，绝不把演示数值作为生产证据。调用示例::

    summary = await MonitoringClient().query_metrics("checkout")
    # 将 summary 与日志、发布记录一起交给 RCA Agent，而不是据此直接自动处置。
"""

import json

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.integrations.http_utils import read_bounded_json

logger = get_logger("integrations.monitoring")

_MOCK_METRICS = (
    "container_memory_usage{{service={service}}}: 最近 30min 从 60% 持续升至 98%（接近 limit）\n"
    "http_request_duration_p99{{service={service}}}: 从 220ms 升至 4.8s\n"
    "http_5xx_rate{{service={service}}}: 从 0.1% 升至 12%\n"
    "pod_restart_total{{service={service}}}: 最近 15min 重启 3 次"
)


class MonitoringClient:
    """面向 RCA 的只读 Prometheus HTTP API 客户端。"""

    def __init__(self, base_url: str | None = None) -> None:
        """使用显式地址或 Settings 中的 Prometheus URL 初始化客户端。"""
        self._base = (base_url or get_settings().prometheus_url).rstrip("/")

    async def query_metrics(self, service: str) -> str:
        """获取服务指标摘要，并在证据源不可用时返回可识别的降级文本。

        Prometheus 即便返回 HTTP 200，也必须同时满足顶层对象和 ``status=success``；否则
        不可信 payload 不会继续流入模型。与日志查询一样，证据失败只降级 RCA，不伪造
        生产数据，也不泄漏原始响应到日志。
        """
        settings = get_settings()
        if not self._base:
            if settings.is_local:
                logger.info("monitoring_mock", service=service)
                return _MOCK_METRICS.format(service=service or "unknown")
            logger.warning("monitoring_unconfigured", service=service)
            return "（监控源未配置，无法获取真实指标）"

        # 查询表达式由服务端模板控制；只插入已转义的 label 值，避免 PromQL 注入。
        safe_service = service.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")
        promql = f'rate(http_requests_total{{service="{safe_service}"}}[5m])'
        try:
            transport = httpx.AsyncHTTPTransport(retries=2)
            timeout = httpx.Timeout(15, connect=5, pool=5)
            async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
                async with client.stream(
                    "GET",
                    f"{self._base}/api/v1/query",
                    params={"query": promql},
                ) as resp:
                    resp.raise_for_status()
                    data = await read_bounded_json(resp, settings.upstream_max_response_bytes)
            if not isinstance(data, dict):
                raise ValueError("Prometheus response must be an object")
            if data.get("status") != "success":
                raise ValueError("Prometheus 返回非 success 状态")
            values = data.get("data", {}).get("result", [])
            prefix = f"PromQL `{promql}` 结果："
            rendered = json.dumps(values, ensure_ascii=False)
            return (prefix + rendered)[: settings.rca_max_evidence_chars]
        except Exception as exc:  # noqa: BLE001 - RCA 降级但不伪造生产证据
            details: dict[str, object] = {"error_type": type(exc).__name__}
            if isinstance(exc, httpx.HTTPStatusError):
                details["status_code"] = exc.response.status_code
            logger.warning("monitoring_query_failed", **details)
            if settings.is_local:
                return _MOCK_METRICS.format(service=service or "unknown")
            return "（监控源查询失败，本次分析没有可用指标证据）"
