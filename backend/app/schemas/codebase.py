"""代码理解相关请求 / 响应模型。"""

from pydantic import BaseModel, Field


class IndexRequest(BaseModel):
    """索引一个本地仓库目录。"""

    repo: str = Field(description="仓库逻辑名（图谱按此隔离）", min_length=1)
    path: str = Field(description="本地仓库绝对路径", min_length=1)


class IndexResponse(BaseModel):
    """索引结果（图规模统计）。"""

    repo: str
    files: int
    functions: int
    calls: int


class CodeQueryRequest(BaseModel):
    """代码库问答。"""

    repo: str = Field(description="仓库逻辑名", min_length=1)
    question: str = Field(description="对代码库的问题", min_length=1)


class ImpactRequest(BaseModel):
    """影响分析。"""

    repo: str = Field(description="仓库逻辑名", min_length=1)
    function: str = Field(description="目标函数名", min_length=1)
    depth: int = Field(default=3, ge=1, le=5, description="反向调用遍历深度")
