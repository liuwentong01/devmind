"""手动 diff 审查与 GitHub PR 异步审查的请求契约。

``POST /api/review/stream`` 接收 ``{"diff":"diff --git ..."}`` 并返回 SSE；
``POST /api/review/trigger`` 接收 ``{"owner":"acme","repo":"service","number":12}``
并将任务放入 ARQ。

生产边界：owner/repo 只能是单个安全 URL segment，diff 也有硬上限；GitHub 仓库权限、
head SHA 固定和回写幂等仍由集成客户端、路由与 worker 共同保证。
"""

from pydantic import Field, field_validator

from app.schemas.base import StrictRequestModel

_GITHUB_NAME_PATTERN = r"^[A-Za-z0-9_.-]{1,100}$"


class ReviewDiffRequest(StrictRequestModel):
    """手动审查请求：直接提交一段 diff。"""

    diff: str = Field(description="统一 diff 文本", min_length=1, max_length=500_000)

    @field_validator("diff")
    @classmethod
    def diff_must_not_be_blank(cls, value: str) -> str:
        """拒绝空白 diff，避免为没有代码变化的请求消耗模型资源。"""
        if not value.strip():
            raise ValueError("diff 不能为空")
        return value


class TriggerReviewRequest(StrictRequestModel):
    """手动触发对某个 GitHub PR 的异步审查（入队）。"""

    owner: str = Field(description="仓库 owner", pattern=_GITHUB_NAME_PATTERN)
    repo: str = Field(description="仓库名", pattern=_GITHUB_NAME_PATTERN)
    number: int = Field(description="PR 编号", gt=0, le=2_147_483_647)
