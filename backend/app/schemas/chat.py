"""Chat 相关的请求 / 响应模型。"""

from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """单条对话消息。"""

    role: Literal["user", "assistant", "system"] = Field(description="消息角色")
    content: str = Field(description="消息内容")


class ChatRequest(BaseModel):
    """发起一次对话的请求体。"""

    messages: list[ChatMessage] = Field(description="完整对话历史，最后一条为本次用户输入")
    model: str | None = Field(default=None, description="可选，覆盖默认 LLM 模型")


# SSE 事件类型说明：
# - token : 增量 token（data 为文本片段）
# - tool  : Agent 调用工具（data 为工具名）
# - done  : 本次回答结束
# - error : 发生错误（data 为错误信息）
SSEEventType = Literal["token", "tool", "done", "error"]
