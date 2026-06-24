"""应用配置：从环境变量 / .env 读取，统一用 Pydantic Settings 管理。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置项。字段名与 .env 中的大写键自动对应（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # 应用
    app_name: str = "DevMind"
    app_env: str = "local"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"

    # LLM 网关
    llm_default_model: str = "deepseek/deepseek-chat"
    llm_temperature: float = 0.3

    # Embedding（RAG 向量化，需与所选模型的维度匹配）
    embedding_model: str = "dashscope/text-embedding-v3"
    embedding_dim: int = 1024

    # RAG 检索参数
    rag_chunk_size: int = 500
    rag_chunk_overlap: int = 80
    rag_top_k: int = 4

    # 代码审查 Agent（P2）
    github_api_base: str = "https://api.github.com"
    github_token: str = ""
    github_webhook_secret: str = ""
    review_rule_source: str = "review-rule"  # 团队审查规范在知识库中的来源标识
    review_top_k: int = 4

    # 数据库 / 缓存（P0 预留）
    database_url: str = "postgresql+psycopg://devmind:devmind@localhost:5432/devmind"
    redis_url: str = "redis://localhost:6379/0"

    # 可观测（Langfuse，可选）
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        """把逗号分隔的 CORS 配置拆成列表。"""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def langfuse_enabled(self) -> bool:
        """两个密钥都配置时才启用 Langfuse 追踪。"""
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache
def get_settings() -> Settings:
    """单例配置，避免重复读取 .env。"""
    return Settings()
