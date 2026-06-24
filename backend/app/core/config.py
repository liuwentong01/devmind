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

    # 告警根因 Agent（P3，AIOps）
    prometheus_url: str = ""  # 留空则用 mock 指标
    loki_url: str = ""  # 留空则用 mock 日志
    deploy_repo: str = ""  # "owner/repo"，用于拉取近期发布 commit；留空则用 mock
    im_webhook_url: str = ""  # 飞书/Slack 群机器人 webhook，留空则只记日志
    rca_max_iterations: int = 2  # 假设验证循环最大轮数
    rca_confidence_threshold: float = 0.75  # 置信度达标即收敛

    # 代码理解 Agent（P5）
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "devmind-neo4j"
    code_index_max_files: int = 800  # 单次索引文件数上限，防止超大仓库卡死

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
