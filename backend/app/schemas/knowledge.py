"""知识库相关请求 / 响应模型。"""

from datetime import datetime

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    """文档入库请求。"""

    title: str = Field(description="文档标题", min_length=1, max_length=512)
    content: str = Field(description="文档正文", min_length=1)
    source: str = Field(default="manual", description="来源标识")


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


class QueryRequest(BaseModel):
    """知识问答请求。"""

    question: str = Field(description="用户问题", min_length=1)
    top_k: int | None = Field(default=None, description="召回切片数，默认读配置")
