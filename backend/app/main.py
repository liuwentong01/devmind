"""FastAPI 应用入口。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.db import init_db
from app.core.logging import get_logger, setup_logging
from app.gateway.admin_routes import router as admin_router
from app.gateway.codebase_routes import router as codebase_router
from app.gateway.incident_routes import router as incident_router
from app.gateway.knowledge_routes import router as knowledge_router
from app.gateway.requirement_routes import router as requirement_router
from app.gateway.review_routes import router as review_router
from app.gateway.routes import router
from app.gateway.webhook_routes import router as webhook_router
from app.kernel.cost import install_cost_tracking
from app.kernel.graph.store import close_graph
from app.kernel.queue import close_queue


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动 / 关闭钩子。"""
    setup_logging()
    settings = get_settings()
    logger = get_logger("main")
    install_cost_tracking()
    # 初始化数据库（启用 pgvector 扩展 + 建表）；失败不阻断启动，便于纯 P0 场景调试
    try:
        await init_db()
    except Exception as exc:  # noqa: BLE001
        logger.warning("db_init_skipped", error=str(exc))
    logger.info(
        "app_start",
        app=settings.app_name,
        env=settings.app_env,
        llm_model=settings.llm_default_model,
        embedding_model=settings.embedding_model,
        langfuse=settings.langfuse_enabled,
    )
    yield
    await close_queue()
    await close_graph()
    logger.info("app_stop")


def create_app() -> FastAPI:
    """工厂函数：创建并配置 FastAPI 实例。"""
    settings = get_settings()
    app = FastAPI(
        title=f"{settings.app_name} API",
        version="0.1.0",
        description="研发效能智能体平台 · P0 内核",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router, prefix="/api")
    app.include_router(knowledge_router, prefix="/api")
    app.include_router(review_router, prefix="/api")
    app.include_router(incident_router, prefix="/api")
    app.include_router(codebase_router, prefix="/api")
    app.include_router(requirement_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")
    app.include_router(webhook_router, prefix="/api")
    return app


app = create_app()
