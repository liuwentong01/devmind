"""需求拆解相关请求 / 响应模型。"""

from pydantic import BaseModel, Field, model_validator


class TaskItem(BaseModel):
    """一条开发任务。"""

    title: str
    type: str = "backend"
    description: str = ""
    story_points: int | None = None
    acceptance_criteria: str = ""


class PlanRequest(BaseModel):
    """拆解请求：提供 PRD 文本或 Confluence 链接二选一。"""

    prd_text: str = Field(default="", description="PRD 正文")
    confluence_url: str = Field(default="", description="Confluence 页面链接/ID")
    project: str = Field(default="", description="Jira 项目 key")
    epic: str = Field(default="", description="挂载到的 Epic key（可选）")

    @model_validator(mode="after")
    def _need_source(self) -> "PlanRequest":
        if not self.prd_text.strip() and not self.confluence_url.strip():
            raise ValueError("prd_text 与 confluence_url 至少提供一个")
        return self


class PlanResponse(BaseModel):
    """拆解结果（HITL 暂停态，尚未写入 Jira）。"""

    thread_id: str
    tasks: list[TaskItem]


class ConfirmRequest(BaseModel):
    """人工确认请求：携带（已编辑的）任务列表恢复工作流。"""

    thread_id: str = Field(description="plan 阶段返回的 thread_id")
    tasks: list[TaskItem] = Field(description="人工确认/编辑后的任务列表")
