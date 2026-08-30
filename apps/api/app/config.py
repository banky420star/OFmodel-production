"""Persona Studio API — Settings & Configuration."""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database — defaults to SQLite for local dev; set DATABASE_URL to postgres for production
    DATABASE_URL: str = "sqlite+aiosqlite:///./persona_studio.db"
    DATABASE_URL_SYNC: str = "sqlite:///./persona_studio.db"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # MinIO / Object Storage
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "persona"
    MINIO_SECRET_KEY: str = "persona_secret"
    MINIO_BUCKET: str = "persona-studio"
    MINIO_SECURE: bool = False

    # Application
    ENVIRONMENT: str = "development"
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # Provider configuration
    PROVIDER_REGISTRY: str = "mock"  # mock | comfyui | elevenlabs | wan | ollama

    # Workflow engine
    MAX_CONCURRENT_WORKFLOWS: int = 10
    WORKFLOW_STEP_TIMEOUT_SECONDS: int = 300

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
