"""知识文档管理与 RAG 问答接口的数据契约。

调用链：``IngestRequest`` 经 ``POST /api/knowledge/documents`` 分块并向量化；
``QueryRequest`` 经 ``POST /api/knowledge/query/stream`` 召回切片并生成回答。入库示例：
``{"title":"发布规范","content":"生产发布必须双人审批","source":"handbook"}``。

生产边界：正文上限用于保护请求内存和 Embedding 成本；``source`` 是检索隔离标签而非访问
控制主体。真正的文档级权限/租户隔离仍需在企业身份层和查询条件中实现。
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.base import StrictRequestModel


class IngestRequest(StrictRequestModel):
    """文档入库请求。"""

    title: str = Field(description="文档标题", min_length=1, max_length=512)
    content: str = Field(description="文档正文", min_length=1, max_length=500_000)
    source: str = Field(
        default="manual",
        description="来源标识",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$",
    )

    @field_validator("title", "content")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        """拒绝只有空白的标题/正文，避免产生不可检索的空知识切片。"""
        if not value.strip():
            raise ValueError("文本不能为空")
        return value


class DocumentOut(BaseModel):
    """文档列表项。"""

    id: int
    title: str
    source: str
    chunk_count: int
    created_at: datetime


class IngestResponse(BaseModel):
    """入库结果。"""

    id: int
    title: str
    chunk_count: int


class QueryRequest(StrictRequestModel):
    """知识问答请求。"""

    question: str = Field(description="用户问题", min_length=1, max_length=8000)
    top_k: int | None = Field(default=None, ge=1, le=20, description="召回切片数，默认读配置")

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        """拒绝空白问题，避免无意义的 Embedding 与 LLM 调用。"""
        if not value.strip():
            raise ValueError("问题不能为空")
        return value
