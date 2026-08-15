"""HTTP 边界使用的 Pydantic 请求/响应模型。

请求模型通常继承 ``StrictRequestModel``，由 FastAPI 在进入路由前完成类型、长度和未知
字段校验；响应模型使用普通 ``BaseModel`` 做稳定序列化。示例：
``ChatRequest.model_validate({"messages": [{"role": "user", "content": "你好"}]})``。

生产边界：Schema 是第一层而非唯一安全边界；涉及路径、权限、外部资源或跨记录一致性的
规则仍须在服务层校验。新增自由文本时必须给出合理长度上限，避免无界内存和 LLM 成本。
"""
