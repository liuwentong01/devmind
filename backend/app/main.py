"""DevMind FastAPI 应用工厂、生命周期与全局错误边界。

启动链：``uvicorn app.main:app`` 导入模块 → ``create_app`` 注册中间件/路由 → lifespan 初始化
日志、成本追踪和数据库 → 接收流量；退出时并行 flush/关闭 Langfuse、ARQ、Neo4j、Postgres。
本地验证示例：``curl http://localhost:8000/api/health``；受保护接口需附带
``X-API-Key``（local 默认可关闭鉴权）。

生产边界：公开 liveness/readiness，普通 API 使用 service API key，``/api/admin`` 使用独立
管理 key，Webhook 自行验签；终端用户身份和租户 RBAC 应由可信 OIDC/IAP 入口实现。非本地
数据库或配置校验失败会阻止启动，不能为“先上线再修”而吞掉失败。
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import get_settings
from app.core.db import close_db, init_db
from app.core.logging import get_logger, setup_logging
from app.core.observability import close_observability
from app.core.security import (
    RequestBodyLimitMiddleware,
    SecurityHeadersMiddleware,
    require_admin_api_key,
    require_api_key,
    security_response_headers,
)
from app.gateway.admin_routes import router as admin_router
from app.gateway.codebase_routes import router as codebase_router
from app.gateway.incident_routes import router as incident_router
from app.gateway.knowledge_routes import router as knowledge_router
from app.gateway.requirement_routes import router as requirement_router
from app.gateway.review_routes import router as review_router
from app.gateway.routes import health_router, router
from app.gateway.webhook_routes import router as webhook_router
from app.kernel.cost import install_cost_tracking
from app.kernel.graph.store import close_graph
from app.kernel.queue import close_queue


@asynccontextmanager
async def lifespan(app: FastAPI):
    """管理进程级资源的启动和关闭；每个 Uvicorn worker 各执行一次。

    ``app`` 参数由 FastAPI 生命周期协议传入，目前无需写入 state，保留它以符合协议签名。
    """
    setup_logging()
    settings = get_settings()
    logger = get_logger("main")
    install_cost_tracking()
    # 本地允许在未启动依赖时体验纯 P0；非本地环境必须 fail closed，避免接流量后才报错。
    try:
        await init_db()
    except Exception as exc:  # noqa: BLE001
        if not settings.is_local:
            logger.error("db_init_failed", exc_info=True)
            raise RuntimeError("database initialization failed") from exc
        logger.warning("db_init_skipped", error_type=type(exc).__name__)
    logger.info(
        "app_start",
        app=settings.app_name,
        env=settings.app_env,
        llm_model=settings.llm_default_model,
        embedding_model=settings.embedding_model,
        langfuse=settings.langfuse_enabled,
        cost_tracking_backend=settings.resolved_cost_tracking_backend,
    )
    try:
        yield
    finally:
        # 各资源彼此独立，滚动退出时并行关闭可缩短 termination grace；return_exceptions 确保
        # 一个 exporter 卡住/失败不会跳过数据库等其余资源的释放。
        results = await asyncio.gather(
            close_observability(),
            close_queue(),
            close_graph(),
            close_db(),
            return_exceptions=True,
        )
        resources = ("observability", "queue", "graph", "database")
        for resource, result in zip(resources, results, strict=True):
            if isinstance(result, BaseException):
                logger.error(
                    "resource_close_failed",
                    resource=resource,
                    error_type=type(result).__name__,
                )
        logger.info("app_stop")


def create_app() -> FastAPI:
    """创建一个完整 FastAPI 实例，供 Uvicorn、测试 ``TestClient`` 或 ASGI server 使用。

    测试可写 ``client = TestClient(create_app())``；配置由缓存单例读取，如测试临时修改环境，
    应先清理 ``get_settings`` 缓存。
    """
    settings = get_settings()
    app = FastAPI(
        title=f"{settings.app_name} API",
        version="0.1.0",
        description="研发效能智能体平台 · P0 内核",
        lifespan=lifespan,
        docs_url="/docs" if settings.api_docs_enabled else None,
        redoc_url="/redoc" if settings.api_docs_enabled else None,
        openapi_url="/openapi.json" if settings.api_docs_enabled else None,
    )

    # CORS 只决定浏览器是否允许跨源读取响应，不是身份验证。生产 origin 已在 Settings 中
    # 限制为显式 HTTPS 列表，且不携带 cookie（allow_credentials=False）。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "Last-Event-ID", "X-API-Key"],
        max_age=600,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)
    app.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=settings.max_request_body_bytes)
    app.add_middleware(
        SecurityHeadersMiddleware,
        strict_transport_security=not settings.is_local,
    )

    # 路由组刻意分为四种边界：公开探针、普通 key、管理 key、协议自身验签的 Webhook。
    # 不要给 webhook 叠加浏览器 API key，否则 GitHub/Alertmanager 无法按其标准协议投递。
    protected = [Depends(require_api_key)]
    app.include_router(health_router, prefix="/api")
    app.include_router(router, prefix="/api", dependencies=protected)
    app.include_router(knowledge_router, prefix="/api", dependencies=protected)
    app.include_router(review_router, prefix="/api", dependencies=protected)
    app.include_router(incident_router, prefix="/api", dependencies=protected)
    app.include_router(codebase_router, prefix="/api", dependencies=protected)
    app.include_router(requirement_router, prefix="/api", dependencies=protected)
    app.include_router(
        admin_router,
        prefix="/api",
        dependencies=[Depends(require_admin_api_key)],
    )
    app.include_router(webhook_router, prefix="/api")

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """返回字段级错误但移除原始 input，避免回显 PRD、diff 或敏感正文。

        Pydantic 原始错误常携带被拒绝的 ``input``；这里只保留定位、类型和说明，既方便客户端
        修正字段，又不会把敏感内容复制到响应或结构化日志。
        """
        errors = [
            {
                "type": error.get("type", "validation_error"),
                "loc": error.get("loc", ()),
                "msg": error.get("msg", "输入无效"),
            }
            for error in exc.errors()
        ]
        get_logger("main").info(
            "request_validation_failed",
            method=request.method,
            path=request.url.path,
            error_count=len(errors),
        )
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, _exc: Exception) -> JSONResponse:
        """记录完整服务端异常，客户端只收到稳定文案与可关联的 request id。

        已知业务错误应在路由中转换为明确 4xx/5xx；落到这里的一律不回显异常字符串，因为
        驱动、URL 或第三方 SDK 的错误可能包含凭据和内部拓扑。
        """
        request_id = getattr(request.state, "request_id", "unknown")
        get_logger("main").error(
            "unhandled_request_error",
            method=request.method,
            path=request.url.path,
            request_id=request_id,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "服务内部错误", "request_id": request_id},
            headers=security_response_headers(
                request_id,
                strict_transport_security=not settings.is_local,
            ),
        )

    return app


app = create_app()
