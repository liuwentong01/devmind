"""集中读取、派生并校验 DevMind 的运行配置。

调用链：第一次 ``get_settings()`` 时，Pydantic 从进程环境和当前目录 ``.env`` 构造
``Settings``，随后 API、worker、迁移和集成层共享缓存实例。代码示例：
``settings = get_settings(); print(settings.normalized_app_env)``。本地可设置
``APP_ENV=local``；生产应从 Secret/ConfigMap 注入，而不是提交真实 ``.env``。

安全策略是“本地易用、非本地 fail closed”：local/test 默认关闭 API 鉴权并允许未签名
Webhook；未知或非本地环境按生产规则处理，缺少凭据、TLS、可信 Host、迁移配置等会在进程
启动前报错。字段约束主要保护单请求资源与配置结构，不替代外部 Secret 管理、网络策略、
租户授权和凭据轮换。
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCAL_ENVS = frozenset({"local", "dev", "development", "test"})
_API_KEY_RE = re.compile(r"^[A-Za-z0-9._~-]{32,256}$")
_TRUSTED_HOST_RE = re.compile(r"^(?:\*\.)?[A-Za-z0-9.-]+$")
_GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")


def _split_csv(value: str) -> list[str]:
    """解析逗号分隔配置，同时去重并保持声明顺序。"""
    return list(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))


class Settings(BaseSettings):
    """全局强类型配置；字段名自动对应大小写不敏感的环境变量。

    例如 ``llm_default_model`` 对应 ``LLM_DEFAULT_MODEL``。带 ``repr=False`` 的字段不会在
    对象 repr 中出现，但依旧属于敏感值，禁止主动记录、返回给 API 或写入前端配置。
    """

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

    # API / HTTP 安全边界
    # AUTH_ENABLED 留空时：local/test=False，其它环境=True。
    auth_enabled: bool | None = None
    devmind_api_keys: str = Field(default="", repr=False)
    devmind_admin_api_keys: str = Field(default="", repr=False)
    trusted_hosts: str = "*"
    expose_api_docs: bool | None = None
    max_request_body_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    max_webhook_body_bytes: int = Field(default=1024 * 1024, ge=1024, le=10 * 1024 * 1024)
    max_alerts_per_webhook: int = Field(default=100, ge=1, le=1000)
    require_tls_upstreams: bool | None = None
    upstream_max_response_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=10 * 1024 * 1024)
    rca_max_evidence_chars: int = Field(default=12_000, ge=1000, le=100_000)

    # Webhook 安全。GitHub 使用其标准 HMAC 签名；告警入口使用 Bearer secret。
    alert_webhook_secret: str = Field(default="", repr=False)
    allow_unsigned_webhooks: bool | None = None

    # LLM 网关
    llm_default_model: str = "deepseek/deepseek-chat"
    llm_temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    llm_request_timeout: float = Field(default=60.0, ge=1.0, le=300.0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_max_tokens: int = Field(default=4096, ge=128, le=32768)
    agent_recursion_limit: int = Field(default=12, ge=4, le=50)
    sse_max_duration_seconds: float = Field(default=600.0, ge=30.0, le=3600.0)
    deepseek_api_key: str = Field(default="", repr=False)
    dashscope_api_key: str = Field(default="", repr=False)
    zhipuai_api_key: str = Field(default="", repr=False)
    openai_api_key: str = Field(default="", repr=False)
    # 自定义 LiteLLM provider 可能使用工作负载身份；需显式跳过已知 key 校验。
    allow_unvalidated_model_provider: bool = False

    # Embedding（RAG 向量化，需与所选模型的维度匹配）
    embedding_model: str = "dashscope/text-embedding-v3"
    embedding_dim: int = Field(default=1024, ge=1, le=8192)

    # RAG 检索参数
    rag_chunk_size: int = Field(default=500, ge=100, le=10_000)
    rag_chunk_overlap: int = Field(default=80, ge=0, le=5_000)
    rag_top_k: int = Field(default=4, ge=1, le=100)

    # 代码审查 Agent（P2）
    github_api_base: str = "https://api.github.com"
    github_token: str = Field(default="", repr=False)
    github_webhook_secret: str = Field(default="", repr=False)
    review_rule_source: str = "review-rule"  # 团队审查规范在知识库中的来源标识
    review_top_k: int = Field(default=4, ge=1, le=100)

    # 告警根因 Agent（P3，AIOps）
    # local/test 留空时可用 mock 体验；非本地留空会将对应证据标为不可用。
    prometheus_url: str = ""
    loki_url: str = ""
    deploy_repo: str = ""  # "owner/repo"，用于拉取近期发布 commit
    im_webhook_url: str = Field(default="", repr=False)  # URL 本身通常携带 secret
    rca_max_iterations: int = Field(default=2, ge=1, le=10)  # 假设验证循环最大轮数
    rca_confidence_threshold: float = Field(default=0.75, ge=0.0, le=1.0)

    # 代码理解 Agent（P5）
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = Field(default="devmind-neo4j", repr=False)
    code_index_max_files: int = Field(default=800, ge=1, le=100_000)
    code_index_max_file_bytes: int = Field(default=1024 * 1024, ge=4096, le=10 * 1024 * 1024)
    code_index_max_total_bytes: int = Field(
        default=64 * 1024 * 1024,
        ge=1024 * 1024,
        le=1024 * 1024 * 1024,
    )
    code_index_max_functions: int = Field(default=50_000, ge=1, le=1_000_000)
    code_index_max_calls: int = Field(default=200_000, ge=1, le=2_000_000)
    # 生产环境必须显式配置；为空时 local/test 可索引任意本地目录。
    code_index_allowed_roots: str = ""

    # 需求拆解 Agent（P4，Atlassian）
    # 如 https://your.atlassian.net；local/test 可显式 dry-run，非本地配置不全会失败。
    atlassian_base_url: str = ""
    atlassian_email: str = ""
    atlassian_api_token: str = Field(default="", repr=False)
    jira_default_project: str = ""  # Jira 项目 key，如 DEV
    allow_jira_dry_run: bool | None = None
    confluence_max_response_bytes: int = Field(
        default=2 * 1024 * 1024, ge=1024, le=10 * 1024 * 1024
    )
    confluence_max_text_chars: int = Field(default=100_000, ge=1000, le=100_000)

    # 数据库 / 缓存：P1/P2/P3/P4 持久化、检索、队列和跨副本状态均依赖它们。
    database_url: str = Field(
        default="postgresql+psycopg://devmind:devmind@localhost:5432/devmind", repr=False
    )
    # 仅迁移进程使用的高权限连接；为空时 Alembic 回退到 DATABASE_URL。
    migration_database_url: str = Field(default="", repr=False)
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=20, ge=0, le=200)
    db_pool_timeout: float = Field(default=30.0, ge=1.0, le=300.0)
    db_pool_recycle: int = Field(default=1800, ge=30, le=86400)
    db_connect_timeout: float = Field(default=10.0, ge=1.0, le=120.0)
    redis_url: str = Field(default="redis://localhost:6379/0", repr=False)

    # 成本聚合：本地默认内存，非本地默认 Redis；可显式关闭。
    # 非本地禁止退回单进程内存，否则多副本查询会得到不完整数据。
    cost_tracking_backend: Literal["disabled", "memory", "redis"] | None = None
    cost_redis_key: str = Field(
        default="devmind:cost:v1",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9:_.-]+$",
    )

    # 可观测（Langfuse，可选）
    langfuse_public_key: str = ""
    langfuse_secret_key: str = Field(default="", repr=False)
    langfuse_host: str = "http://localhost:3000"

    @property
    def normalized_app_env(self) -> str:
        """标准化环境名，未知环境按非本地环境应用安全默认值。"""
        return self.app_env.strip().lower()

    @property
    def is_local(self) -> bool:
        """是否属于允许开发便利行为的 ``local/test`` 环境。"""
        return self.normalized_app_env in _LOCAL_ENVS

    @property
    def api_auth_enabled(self) -> bool:
        """是否启用 API key 鉴权。"""
        return self.auth_enabled if self.auth_enabled is not None else not self.is_local

    @property
    def api_docs_enabled(self) -> bool:
        """OpenAPI/Swagger 默认仅在本地和测试环境开放。"""
        return self.expose_api_docs if self.expose_api_docs is not None else self.is_local

    @property
    def tls_upstreams_required(self) -> bool:
        """上游 TLS 默认在非本地环境强制启用。"""
        if self.require_tls_upstreams is not None:
            return self.require_tls_upstreams
        return not self.is_local

    @property
    def unsigned_webhooks_allowed(self) -> bool:
        """仅本地/测试默认允许无密钥 Webhook，非本地环境不可开启。"""
        if self.allow_unsigned_webhooks is not None:
            return self.allow_unsigned_webhooks
        return self.is_local

    @property
    def jira_dry_run_allowed(self) -> bool:
        """Jira dry-run 仅作为本地演示能力，生产环境始终禁用。"""
        if self.allow_jira_dry_run is not None:
            return self.allow_jira_dry_run
        return self.is_local

    @property
    def api_key_list(self) -> list[str]:
        """解析普通服务密钥列表；支持以 CSV 同时保留新旧密钥做轮换。"""
        return _split_csv(self.devmind_api_keys)

    @property
    def admin_api_key_list(self) -> list[str]:
        """解析管理密钥列表；它必须与浏览器和普通服务调用凭据隔离。"""
        return _split_csv(self.devmind_admin_api_keys)

    @property
    def trusted_host_list(self) -> list[str]:
        """解析允许到达 ASGI 应用的 Host，供 ``TrustedHostMiddleware`` 使用。"""
        return _split_csv(self.trusted_hosts)

    @property
    def code_index_allowed_root_list(self) -> list[str]:
        """解析代码索引根目录白名单；真正授权仍会对 canonical path 再校验。"""
        return _split_csv(self.code_index_allowed_roots)

    @property
    def cors_origin_list(self) -> list[str]:
        """把逗号分隔的 CORS 配置拆成列表。"""
        return _split_csv(self.cors_origins)

    @property
    def langfuse_enabled(self) -> bool:
        """两个密钥都配置时才启用 Langfuse 追踪。"""
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    def api_key_for_model(self, model: str) -> str | None:
        """返回已知 LiteLLM provider 对应的密钥。

        Settings 可以从 ``.env`` 读取配置，但第三方 SDK 通常只检查真实进程环境。
        调用方显式传入密钥，既保证本地 ``.env`` 可用，也避免依赖 SDK 的隐式加载行为。
        """
        provider_keys = {
            "deepseek/": self.deepseek_api_key,
            "dashscope/": self.dashscope_api_key,
            "zhipuai/": self.zhipuai_api_key,
            "openai/": self.openai_api_key,
        }
        for prefix, api_key in provider_keys.items():
            if model.startswith(prefix):
                return api_key or None
        return None

    @property
    def resolved_cost_tracking_backend(self) -> Literal["disabled", "memory", "redis"]:
        """应用环境默认值：本地用内存，非本地用跨副本共享的 Redis。"""
        if self.cost_tracking_backend is not None:
            return self.cost_tracking_backend
        return "memory" if self.is_local else "redis"

    @model_validator(mode="after")
    def _validate_security_boundary(self) -> "Settings":
        """交叉校验字段，并阻止非本地环境以不安全配置启动。

        这里故意在配置构造阶段集中失败：比服务先接流量、直到某条冷路径才发现未验签或使用
        默认密码更安全。测试如需覆盖不同环境，应构造 ``Settings(_env_file=None, ...)``，
        不要修改全局缓存后依赖测试顺序。
        """
        if self.max_webhook_body_bytes > self.max_request_body_bytes:
            raise ValueError("MAX_WEBHOOK_BODY_BYTES 不能大于 MAX_REQUEST_BODY_BYTES")
        if self.rag_chunk_overlap >= self.rag_chunk_size:
            raise ValueError("RAG_CHUNK_OVERLAP 必须小于 RAG_CHUNK_SIZE")
        if self.confluence_max_text_chars > self.confluence_max_response_bytes:
            raise ValueError("CONFLUENCE_MAX_TEXT_CHARS 不能大于 CONFLUENCE_MAX_RESPONSE_BYTES")
        if self.deploy_repo and not _GITHUB_REPO_RE.fullmatch(self.deploy_repo):
            raise ValueError("DEPLOY_REPO 必须是安全的 owner/repo 格式")
        langfuse_parts = (bool(self.langfuse_public_key), bool(self.langfuse_secret_key))
        if any(langfuse_parts) and not all(langfuse_parts):
            raise ValueError("LANGFUSE_PUBLIC_KEY 与 LANGFUSE_SECRET_KEY 必须同时配置")
        if self.langfuse_enabled and not self.langfuse_host.strip():
            raise ValueError("启用 Langfuse 时必须配置 LANGFUSE_HOST")

        configured_keys = self.api_key_list + self.admin_api_key_list
        if any(not _API_KEY_RE.fullmatch(key) for key in configured_keys):
            raise ValueError("API key 必须为 32..256 位，且只能包含字母、数字及 . _ ~ -")
        if self.api_auth_enabled and not (self.api_key_list and self.admin_api_key_list):
            raise ValueError("启用鉴权时必须同时配置 DEVMIND_API_KEYS 和 DEVMIND_ADMIN_API_KEYS")
        if set(self.api_key_list) & set(self.admin_api_key_list):
            raise ValueError("普通 API key 与管理 API key 不得复用")

        # 以上结构性约束对所有环境都成立；本地只跳过下面的生产必需项，方便无外部依赖体验。
        if self.is_local:
            return self

        if not self.api_auth_enabled:
            raise ValueError("非本地环境禁止关闭 API 鉴权")
        if not self.tls_upstreams_required:
            raise ValueError("非本地环境禁止 REQUIRE_TLS_UPSTREAMS=false")
        if self.unsigned_webhooks_allowed:
            raise ValueError("非本地环境禁止 ALLOW_UNSIGNED_WEBHOOKS=true")
        if self.jira_dry_run_allowed:
            raise ValueError("非本地环境禁止 ALLOW_JIRA_DRY_RUN=true")
        atlassian_parts = (
            bool(self.atlassian_base_url),
            bool(self.atlassian_email),
            bool(self.atlassian_api_token),
        )
        if any(atlassian_parts) and not all(atlassian_parts):
            raise ValueError("非本地 Atlassian 集成必须同时配置 BASE_URL、EMAIL 和 API_TOKEN")
        if not self.trusted_host_list or "*" in self.trusted_host_list:
            raise ValueError("非本地环境必须显式配置 TRUSTED_HOSTS，且不能包含通配符 *")
        if any(not _TRUSTED_HOST_RE.fullmatch(host) for host in self.trusted_host_list):
            raise ValueError("TRUSTED_HOSTS 包含无效主机名")
        if not self.cors_origin_list or "*" in self.cors_origin_list:
            raise ValueError("非本地环境必须显式配置 CORS_ORIGINS，且不能包含通配符 *")
        if any(not origin.startswith("https://") for origin in self.cors_origin_list):
            raise ValueError("非本地环境的 CORS_ORIGINS 必须使用 https://")
        for origin in self.cors_origin_list:
            parsed_origin = urlsplit(origin)
            try:
                _ = parsed_origin.port
            except ValueError as exc:
                raise ValueError("CORS_ORIGINS 包含无效端口") from exc
            if (
                parsed_origin.scheme.lower() != "https"
                or not parsed_origin.hostname
                or parsed_origin.username is not None
                or parsed_origin.password is not None
                or parsed_origin.path
                or parsed_origin.query
                or parsed_origin.fragment
                or any(ord(char) < 33 or ord(char) == 127 for char in origin)
            ):
                raise ValueError("CORS_ORIGINS 必须是无路径/凭据/参数的合法 origin")
        if not self.github_webhook_secret or len(self.github_webhook_secret) < 32:
            raise ValueError("非本地环境必须配置至少 32 字符的 GITHUB_WEBHOOK_SECRET")
        if not self.alert_webhook_secret or len(self.alert_webhook_secret) < 32:
            raise ValueError("非本地环境必须配置至少 32 字符的 ALERT_WEBHOOK_SECRET")
        if not self.github_token:
            raise ValueError("非本地环境必须配置 GITHUB_TOKEN")
        if not self.im_webhook_url:
            raise ValueError("非本地环境必须配置 IM_WEBHOOK_URL")
        if not self.code_index_allowed_root_list:
            raise ValueError("非本地环境必须配置 CODE_INDEX_ALLOWED_ROOTS")
        if any(
            not Path(root).is_absolute() or Path(root) == Path("/")
            for root in self.code_index_allowed_root_list
        ):
            raise ValueError("CODE_INDEX_ALLOWED_ROOTS 必须是绝对目录且不能直接允许根目录 /")

        provider_keys = {
            "deepseek/": self.deepseek_api_key,
            "dashscope/": self.dashscope_api_key,
            "zhipuai/": self.zhipuai_api_key,
            "openai/": self.openai_api_key,
        }
        for config_name, model in (
            ("LLM_DEFAULT_MODEL", self.llm_default_model),
            ("EMBEDDING_MODEL", self.embedding_model),
        ):
            matching_keys = [
                key for prefix, key in provider_keys.items() if model.startswith(prefix)
            ]
            if matching_keys:
                if not matching_keys[0]:
                    raise ValueError(f"非本地 {config_name} 对应的 provider API key 未配置")
            elif not self.allow_unvalidated_model_provider:
                raise ValueError(
                    f"非本地 {config_name} provider 未纳入凭据校验；"
                    "自定义 provider 需显式设置 ALLOW_UNVALIDATED_MODEL_PROVIDER=true"
                )

        self._validate_postgres_url(self.database_url, "DATABASE_URL")
        if self.migration_database_url:
            self._validate_postgres_url(
                self.migration_database_url,
                "MIGRATION_DATABASE_URL",
            )
        if not self.neo4j_password or self.neo4j_password == "devmind-neo4j":
            raise ValueError("非本地环境禁止使用空密码或默认 Neo4j 凭据")
        if self.tls_upstreams_required:
            self._validate_tls_upstreams()
        if self.resolved_cost_tracking_backend == "memory":
            raise ValueError("非本地环境的 COST_TRACKING_BACKEND 不能使用 memory")
        return self

    def _validate_postgres_url(self, value: str, name: str):
        """校验生产 PostgreSQL 连接结构、非默认凭据与 TLS 模式。

        要求显式 ``postgresql+psycopg``，避免 SQLAlchemy 根据含糊 scheme 选择不同同步/
        异步驱动；解析后的密码只用于判空/默认值比较，不进入异常文本。
        """
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"非本地 {name} 端口无效") from exc
        if (
            parsed.scheme.lower() != "postgresql+psycopg"
            or not parsed.hostname
            or port is None
            or not parsed.path.strip("/")
        ):
            raise ValueError(f"非本地 {name} 必须是完整的 postgresql+psycopg URL")
        db_user = unquote(parsed.username or "")
        db_password = unquote(parsed.password or "")
        if not db_user or not db_password or (db_user == "devmind" and db_password == "devmind"):
            raise ValueError(f"非本地 {name} 禁止使用空账号/密码或默认数据库凭据")
        if self.tls_upstreams_required:
            ssl_modes = parse_qs(parsed.query).get("sslmode", [])
            if len(ssl_modes) != 1 or ssl_modes[0] not in {
                "require",
                "verify-ca",
                "verify-full",
            }:
                raise ValueError(f"非本地 {name} 必须配置 sslmode=require/verify-ca/verify-full")
        return parsed

    def _validate_tls_upstreams(self) -> None:
        """要求所有携带凭据或业务数据的生产上游启用传输加密。

        URL 不允许内嵌不必要的凭据、fragment 或 base URL query，减少日志/重定向泄露与路径
        拼接歧义；这只验证配置形态，证书信任链仍由系统 CA、驱动和部署环境负责。
        """
        redis = urlsplit(self.redis_url)
        try:
            _ = redis.port
        except ValueError as exc:
            raise ValueError("非本地 REDIS_URL 端口无效") from exc
        if redis.scheme.lower() != "rediss":
            raise ValueError("非本地 REDIS_URL 必须使用 rediss://")
        if not redis.hostname or not redis.password or redis.fragment:
            raise ValueError("非本地 REDIS_URL 必须是带主机和认证密码、无 fragment 的完整地址")
        neo4j = urlsplit(self.neo4j_uri)
        try:
            _ = neo4j.port
        except ValueError as exc:
            raise ValueError("非本地 NEO4J_URI 端口无效") from exc
        if neo4j.scheme.lower() not in {"neo4j+s", "bolt+s"}:
            raise ValueError("非本地 NEO4J_URI 必须使用 neo4j+s:// 或 bolt+s://")
        if (
            not neo4j.hostname
            or neo4j.username is not None
            or neo4j.password is not None
            or neo4j.fragment
        ):
            raise ValueError("非本地 NEO4J_URI 必须是无内嵌凭据/fragment 的完整地址")

        base_urls = {
            "GITHUB_API_BASE": self.github_api_base,
            "ATLASSIAN_BASE_URL": self.atlassian_base_url,
            "PROMETHEUS_URL": self.prometheus_url,
            "LOKI_URL": self.loki_url,
            "LANGFUSE_HOST": self.langfuse_host if self.langfuse_enabled else "",
        }
        https_urls = {**base_urls, "IM_WEBHOOK_URL": self.im_webhook_url}
        for name, value in https_urls.items():
            if not value:
                continue
            parsed = urlsplit(value)
            try:
                _ = parsed.port
            except ValueError as exc:
                raise ValueError(f"非本地 {name} 端口无效") from exc
            if (
                parsed.scheme.lower() != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or any(char.isspace() or ord(char) == 127 for char in value)
            ):
                raise ValueError(f"非本地 {name} 必须是无内嵌凭据/fragment 的 https URL")
            if name in base_urls and parsed.query:
                raise ValueError(f"非本地 {name} 是 base URL，不能包含 query 参数")


@lru_cache
def get_settings() -> Settings:
    """返回进程级配置单例，避免每个请求重复解析环境和 ``.env``。

    测试修改环境变量后需要调用 ``get_settings.cache_clear()``；生产运行期间不要动态修改
    环境并期待热更新，配置变更应通过滚动发布生成新进程。
    """
    return Settings()
