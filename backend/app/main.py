"""FastAPI 应用入口。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.gateway.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动 / 关闭钩子。"""
    setup_logging()
    settings = get_settings()
    logger = get_logger("main")
    logger.info(
        "app_start",
        app=settings.app_name,
        env=settings.app_env,
        llm_model=settings.llm_default_model,
        langfuse=settings.langfuse_enabled,
    )
    yield
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
    return app


app = create_app()
