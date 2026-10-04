from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BASE_URL: str = "http://127.0.0.1:8000"
    TURSO_DATABASE_URL: str = "file:local.db"
    TURSO_AUTH_TOKEN: str = ""
    
    APP_SECRET_KEY: str = "secret-key-must-be-configured-in-production-123456"
    TOKEN_ENCRYPTION_KEY: str = ""
    INGEST_API_KEY: str = "secret-ingest-api-key"
    
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD_HASH: str = ""
    SECURE_COOKIES: bool = False
    
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    
    META_APP_ID: str = ""
    META_APP_SECRET: str = ""
    META_GRAPH_VERSION: str = "v21.0"
    
    TIKTOK_CLIENT_KEY: str = ""
    TIKTOK_CLIENT_SECRET: str = ""
    
    N8N_WEBHOOK_URL_YOUTUBE: str = "http://n8n:5678/webhook/sync-youtube"
    N8N_WEBHOOK_URL_INSTAGRAM: str = "http://n8n:5678/webhook/sync-instagram"
    N8N_WEBHOOK_URL_TIKTOK: str = "http://n8n:5678/webhook/sync-tiktok"
    N8N_WEBHOOK_SECRET: str = ""
    
    TZ: str = "Asia/Jakarta"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
