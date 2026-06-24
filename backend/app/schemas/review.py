"""代码审查相关请求 / 响应模型。"""

from pydantic import BaseModel, Field


class ReviewDiffRequest(BaseModel):
    """手动审查请求：直接提交一段 diff。"""

    diff: str = Field(description="统一 diff 文本", min_length=1)


class TriggerReviewRequest(BaseModel):
    """手动触发对某个 GitHub PR 的异步审查（入队）。"""

    owner: str = Field(description="仓库 owner")
    repo: str = Field(description="仓库名")
    number: int = Field(description="PR 编号", gt=0)
