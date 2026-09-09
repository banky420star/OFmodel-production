"""Persona Studio API — Settings & Configuration."""

from pathlib import Path
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

    # Auth gate — shared operator bearer token for /api/v1.
    # Empty/unset = auth disabled (local dev + tests).
    API_AUTH_TOKEN: str = ""

    # Provider configuration
    PROVIDER_REGISTRY: str = "mock"  # mock | comfyui | elevenlabs | wan | ollama

    # External API keys
    ELEVENLABS_API_KEY: str = ""
    WAN_API_KEY: str = ""
    WAN_VIDEO_URL: str = ""
    OLLAMA_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = ""
    COMFYUI_URL: str = ""
    DASHSCOPE_API_KEY: str = ""
    HUGGINGFACE_API_KEY: str = ""
    HF_IMAGE_MODEL: str = "black-forest-labs/FLUX.1-schnell"
    HF_IMG2IMG_MODEL: str = "stabilityai/stable-diffusion-xl-refiner-1.0"

    # Instagram Graph API
    INSTAGRAM_ACCESS_TOKEN: str = ""
    INSTAGRAM_ACCOUNT_ID: str = ""  # The Instagram Business Account ID (numeric)

    # Workflow engine
    MAX_CONCURRENT_WORKFLOWS: int = 10
    WORKFLOW_STEP_TIMEOUT_SECONDS: int = 300

    class Config:
        env_file = str(Path(__file__).resolve().parent.parent.parent.parent / ".env")


@lru_cache
def get_settings() -> Settings:
    return Settings()
