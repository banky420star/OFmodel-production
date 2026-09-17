"""Persona Production Line — Settings & Configuration.

Real providers only: one explicit provider choice per capability. There is
no PROVIDER_REGISTRY mode string and no mock mode — setting the legacy
PROVIDER_REGISTRY variable raises at import.
"""

from pathlib import Path
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database — defaults to SQLite for local dev; set DATABASE_URL to postgres for production
    DATABASE_URL: str = "sqlite+aiosqlite:///./persona_studio.db"

    # Application
    ENVIRONMENT: str = "development"
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    WEB_ORIGIN: str = "http://localhost:3000"

    # ── Provider selection — real providers only, one per capability ──
    LLM_PROVIDER: str = "ollama"                # ollama
    IMAGE_PROVIDER: str = "dashscope"           # dashscope | eachsense | comfyui | huggingface
    VIDEO_PROVIDER: str = "dashscope_wan"       # dashscope_wan | wan_server
    VOICE_PROVIDER: str = "macos_say"           # macos_say | elevenlabs
    TRAINER_PROVIDER: str = "hf"                # hf (local MPS/CUDA LoRA)
    STORAGE_PROVIDER: str = "filesystem"        # filesystem | minio
    MODERATION_PROVIDER: str = "huggingface"    # huggingface (fail-closed)

    # External API keys / endpoints
    ELEVENLABS_API_KEY: str = ""
    WAN_API_KEY: str = ""
    DASHSCOPE_API_KEY: str = ""
    WAN_VIDEO_URL: str = ""
    OLLAMA_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen3:4b"
    COMFYUI_URL: str = ""
    COMFYUI_CHECKPOINT: str = "sd_xl_base_1.0.safetensors"
    # Cold SDXL load from disk is 3-4 min on top of ~2 min sampling; 300s
    # spuriously failed every first generation after a ComfyUI restart.
    COMFYUI_TIMEOUT: int = 900
    HUGGINGFACE_API_KEY: str = ""
    HF_IMAGE_MODEL: str = "black-forest-labs/FLUX.1-schnell"
    HF_IMG2IMG_MODEL: str = "stabilityai/stable-diffusion-xl-refiner-1.0"

    # EachLabs each::sense (adult-capable image generation)
    EACHLABS_API_KEY: str = ""
    EACHSENSE_MODE: str = "max"  # max | eco

    # MinIO (only when STORAGE_PROVIDER=minio)
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    MINIO_BUCKET: str = "persona"
    MINIO_SECURE: bool = False

    # Instagram Graph API (optional analytics)
    INSTAGRAM_ACCESS_TOKEN: str = ""
    INSTAGRAM_ACCOUNT_ID: str = ""

    # Adult content — OFF by default; four-layer gate (see routes/content.py)
    ADULT_CONTENT_ENABLED: bool = False

    # Workflow engine (enforced by app/jobs/runner.py)
    MAX_CONCURRENT_WORKFLOWS: int = 10
    WORKFLOW_STEP_TIMEOUT_SECONDS: int = 600

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if getattr(self, "PROVIDER_REGISTRY", None):
            raise ValueError(
                "PROVIDER_REGISTRY is removed — set explicit per-capability vars "
                "(LLM_PROVIDER, IMAGE_PROVIDER, VIDEO_PROVIDER, VOICE_PROVIDER, "
                "TRAINER_PROVIDER, STORAGE_PROVIDER, MODERATION_PROVIDER). "
                "Mock mode does not exist in this project."
            )

    class Config:
        env_file = str(Path(__file__).resolve().parent.parent.parent.parent / ".env")
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()