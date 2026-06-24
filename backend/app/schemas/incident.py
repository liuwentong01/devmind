"""告警根因相关请求模型。"""

from pydantic import BaseModel, Field


class AlertInput(BaseModel):
    """一条告警（手动模拟或 webhook 解析后的统一结构）。"""

    alertname: str = Field(description="告警名称", min_length=1)
    service: str = Field(default="", description="受影响服务")
    severity: str = Field(default="warning", description="级别")
    description: str = Field(default="", description="告警描述")
    starts_at: str = Field(default="", description="开始时间")

    def to_dict(self) -> dict:
        return self.model_dump()
