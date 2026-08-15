"""需求拆解两阶段人工确认（HITL）的 API 契约。

调用链：先向 ``POST /api/requirement/plan`` 提交 PRD，取得 ``thread_id`` 与任务建议；人工
编辑后把完整任务列表提交到 ``/confirm``，系统才写 Jira。plan 示例：
``{"prd_text":"新增审批流","project":"DEV","epic":"DEV-42"}``。

生产边界：最多 100 项任务，且 Jira key、文本长度和故事点受到约束；``confirm`` 不是局部
补丁，而是对完整任务快照的批准。持久化租约与幂等性由 requirement store 保证。
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.base import StrictRequestModel

MAX_REQUIREMENT_TASKS = 100


class TaskItem(StrictRequestModel):
    """一条开发任务。"""

    title: str = Field(min_length=1, max_length=512)
    type: Literal["backend", "frontend", "task"] = "backend"
    description: str = Field(default="", max_length=20_000)
    story_points: Literal[1, 2, 3, 5, 8] | None = None
    acceptance_criteria: str = Field(default="", max_length=20_000)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        """拒绝只含空白的任务标题，保证 Jira 副作用具有可识别摘要。"""
        if not value.strip():
            raise ValueError("任务标题不能为空")
        return value


class PlanRequest(StrictRequestModel):
    """拆解请求：提供 PRD 文本或 Confluence 链接二选一。"""

    prd_text: str = Field(default="", description="PRD 正文", max_length=100_000)
    confluence_url: str = Field(
        default="",
        description="Confluence 页面链接/ID",
        max_length=2048,
        pattern=r"^[^\x00-\x1f\x7f]*$",
    )
    project: str = Field(
        default="",
        description="Jira 项目 key",
        pattern=r"^(?:[A-Z][A-Z0-9_]{0,31})?$",
    )
    epic: str = Field(
        default="",
        description="挂载到的 Epic key（可选）",
        pattern=r"^(?:[A-Z][A-Z0-9_]{0,31}-[1-9][0-9]{0,9})?$",
    )

    @model_validator(mode="after")
    def _need_source(self) -> "PlanRequest":
        """要求内联 PRD 与 Confluence 来源至少提供一个非空值。"""
        if not self.prd_text.strip() and not self.confluence_url.strip():
            raise ValueError("prd_text 与 confluence_url 至少提供一个")
        return self


class PlanResponse(BaseModel):
    """拆解结果（HITL 暂停态，尚未写入 Jira）。"""

    thread_id: str
    tasks: list[TaskItem] = Field(min_length=1, max_length=MAX_REQUIREMENT_TASKS)


class ConfirmRequest(StrictRequestModel):
    """人工确认请求：携带（已编辑的）任务列表恢复工作流。"""

    thread_id: str = Field(
        description="plan 阶段返回的 thread_id",
        pattern=r"^[0-9a-f]{32}$",
    )
    tasks: list[TaskItem] = Field(
        description="人工确认/编辑后的任务列表",
        min_length=1,
        max_length=MAX_REQUIREMENT_TASKS,
    )
