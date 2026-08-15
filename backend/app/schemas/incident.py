"""根因分析使用的标准化告警输入模型。

手动接口 ``POST /api/incident/analyze/stream`` 与 Alertmanager Webhook 都会生成
``AlertInput``，再转成字典交给 RCA LangGraph。最小示例：
``{"alertname":"HighErrorRate","service":"checkout","severity":"critical"}``。

生产边界：``service`` 会进入 Loki/Prometheus label matcher，因此使用白名单字符集；描述
仍是不可信外部文本，只能作为证据，不能被 LLM 当作系统指令。
"""

from pydantic import Field

from app.schemas.base import StrictRequestModel


class AlertInput(StrictRequestModel):
    """一条告警（手动模拟或 webhook 解析后的统一结构）。"""

    alertname: str = Field(
        description="告警名称",
        min_length=1,
        max_length=256,
        pattern=r"^[^\x00-\x1f\x7f]+$",
    )
    # 该值会进入 Loki/PromQL label matcher，限制字符集以阻断查询注入。
    service: str = Field(
        default="",
        description="受影响服务",
        pattern=r"^(?:[A-Za-z0-9_.:-]{1,128})?$",
    )
    severity: str = Field(
        default="warning",
        description="级别",
        pattern=r"^[A-Za-z0-9_.-]{1,32}$",
    )
    description: str = Field(default="", description="告警描述", max_length=8192)
    starts_at: str = Field(
        default="",
        description="开始时间",
        max_length=128,
        pattern=r"^[^\x00-\x1f\x7f]*$",
    )

    def to_dict(self) -> dict:
        """转为 LangGraph state 可序列化字典；不会附带未声明字段。"""
        return self.model_dump()
