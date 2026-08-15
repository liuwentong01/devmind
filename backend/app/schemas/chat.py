"""通用 Agent 对话接口的请求契约与 SSE 事件类型。

调用链：``POST /api/chat/stream`` → ``ChatRequest`` 校验 → orchestrator Agent → SSE。
请求示例：``{"messages":[{"role":"user","content":"解释这次告警"}]}``；客户端按
``token/tool/done/error`` 事件更新界面。

生产边界：客户端不能提交 ``system`` 角色；单条消息、消息数和总字符数均有限制。这些
限制控制内存与模型上下文成本，但不能替代 ``prompt_security`` 中的不可信输入策略。
"""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.base import StrictRequestModel


class ChatMessage(StrictRequestModel):
    """单条对话消息。"""

    # system 指令只能由服务端定义，禁止客户端伪造高优先级角色。
    role: Literal["user", "assistant"] = Field(description="消息角色")
    content: str = Field(description="消息内容", min_length=1, max_length=16_000)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        """拒绝只含空白的消息；``min_length`` 本身无法覆盖这种输入。"""
        if not value.strip():
            raise ValueError("消息内容不能为空")
        return value


class ChatRequest(StrictRequestModel):
    """发起一次对话的请求体。"""

    messages: list[ChatMessage] = Field(
        description="完整对话历史，最后一条为本次用户输入",
        min_length=1,
        max_length=50,
    )

    @model_validator(mode="after")
    def validate_conversation(self) -> "ChatRequest":
        """确保请求以用户问题结束，并限制整段历史的累计上下文成本。"""
        if self.messages[-1].role != "user":
            raise ValueError("最后一条消息必须来自用户")
        if sum(len(message.content) for message in self.messages) > 64_000:
            raise ValueError("对话历史总长度超过限制")
        return self


# SSE 事件类型说明：
# - token : 增量 token（data 为文本片段）
# - tool  : Agent 调用工具（data 为工具名）
# - done  : 本次回答结束
# - error : 发生错误（data 为错误信息）
SSEEventType = Literal["token", "tool", "done", "error"]
