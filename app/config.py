from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 必需从 .env 读取，不设默认值——缺配置时启动失败而非静默用错数据库/密钥
    DATABASE_URL: str
    JWT_SECRET_KEY: str
    ENCRYPTION_KEY: str  # 上游 API Key 加密，Fernet 格式
    HMAC_SECRET: str  # 客户端 API Key 哈希，至少 32 字节随机串

    JWT_EXPIRE_DAYS: int = 7
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin123"

    # Audit log data policy (see README "数据存储与隐私" + spec §6.3)
    AUDIT_LOG_FULL_BODY: bool = False
    AUDIT_LOG_PREVIEW_CHARS: int = 80
    AUDIT_LOG_RETENTION_DAYS: int = 90
    ENABLE_PII_REDACTION: bool = False

    # CORS allowed origins. Comma-separated string in .env, parsed to list.
    # Example: ALLOWED_ORIGINS="http://localhost:3000,https://gateflow.example.com"
    ALLOWED_ORIGINS: str = "http://localhost:3000"

    # Database connection pool tuning (P0-5). Adjust for your traffic profile.
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_RECYCLE_SECONDS: int = 1800

    # Observability (P2-8)
    # LOG_FORMAT: "text" (dev, console-readable) or "json" (prod, one-line JSON
    # per record incl. request_id). LOG_JSON is not used - LOG_FORMAT only.
    LOG_FORMAT: str = "text"
    # Expose GET /metrics (Prometheus text format). Keep true on intranet
    # deployments; put a reverse proxy with auth in front if exposed publicly.
    METRICS_ENABLED: bool = True

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
