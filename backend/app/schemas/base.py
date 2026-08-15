"""API 入参模型的统一严格基类。

所有面向客户端的请求模型继承 ``StrictRequestModel``，FastAPI 会在调用路由前自动验证。
例如 ``StrictRequestModel`` 的子类遇到拼错字段或 ``"1"`` 代替整数时会返回 422，而不会
静默丢弃/转换。

生产边界：严格类型可以降低契约歧义，但字段级长度、格式以及业务授权仍需由具体 Schema
和服务层补充；响应模型不必继承本类，以免把兼容性策略误用于输出。
"""

from pydantic import BaseModel, ConfigDict


class StrictRequestModel(BaseModel):
    """拒绝未知字段与宽松类型转换，避免拼错字段被静默接受。"""

    model_config = ConfigDict(extra="forbid", strict=True)
