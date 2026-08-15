"""FastAPI HTTP/SSE/Webhook 适配层。

各 ``*_routes.py`` 把已校验的 API 请求转换为 Agent、Kernel 或集成客户端调用，最终由
``app.main.create_app`` 挂载到 ``/api``。例如普通对话路由来自
``app.gateway.routes.router``，Webhook 则来自 ``app.gateway.webhook_routes.router``。

生产边界：gateway 负责协议、鉴权依赖、状态码和错误脱敏，不在此包复制领域逻辑；长耗时
副作用应入 ARQ 队列，流式接口必须通过 ``gateway.sse.stream_response`` 统一设置时限。
"""
