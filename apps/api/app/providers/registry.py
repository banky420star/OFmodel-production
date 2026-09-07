"""Persona Studio — Provider Registry.

Dynamically selects mock or real providers based on configuration.
This is the central place where provider swapping happens.

Environment variables:
  PROVIDER_REGISTRY = mock | comfyui | hybrid
  COMFYUI_URL = http://localhost:8188
  ELEVENLABS_API_KEY = sk_...
  WAN_VIDEO_URL = http://localhost:8080
  OLLAMA_URL = http://localhost:11434
  INSTAGRAM_ACCESS_TOKEN = EAAG...
  INSTAGRAM_ACCOUNT_ID = 17841400123456789
"""

from __future__ import annotations
import os
from typing import Type

from app.providers.base import (
    LLMProvider, ImageProvider, VideoProvider, VoiceProvider,
    TrainerProvider, StorageProvider,
)
from app.providers.mocks import (
    MockLLMProvider, MockImageProvider, MockVideoProvider,
    MockVoiceProvider, MockTrainerProvider, MockStorageProvider,
)


class ProviderRegistry:
    """Central registry that provides the correct provider implementation.

    Usage:
        registry = ProviderRegistry()
        image_provider = registry.get_image_provider()
        result = await image_provider.generate(prompt="...", seed=42)
    """

    def __init__(self, mode: str = ""):
        from app.config import get_settings
        settings = get_settings()
        self._mode = mode or settings.PROVIDER_REGISTRY
        self._settings = settings
        self._instances: dict[str, object] = {}

    def _get(self, key: str, factory):
        if key not in self._instances:
            self._instances[key] = factory()
        return self._instances[key]

    def get_llm_provider(self) -> LLMProvider:
        if self._mode in ("hybrid", "ollama", "openai"):
            try:
                from app.providers.ollama_provider import OllamaLLMProvider
                return self._get("llm", lambda: OllamaLLMProvider(
                    base_url=self._settings.OLLAMA_URL,
                    model=self._settings.OLLAMA_MODEL or "qwen3:8b",
                ))
            except ImportError:
                pass
        return self._get("llm", MockLLMProvider)

    def get_image_provider(self) -> ImageProvider:
        # Priority 1: ComfyUI (local GPU) — only if URL is explicitly set
        if self._mode in ("hybrid", "comfyui") and self._settings.COMFYUI_URL:
            try:
                from app.providers.comfyui import ComfyUIImageProvider
                return self._get("image", lambda: ComfyUIImageProvider(
                    base_url=self._settings.COMFYUI_URL
                ))
            except ImportError:
                pass
        # Priority 2: DashScope Qwen-Image (same key as Wan video, up to 2048px)
        api_key = self._settings.WAN_API_KEY or getattr(self._settings, "DASHSCOPE_API_KEY", "")
        if api_key:
            try:
                from app.providers.dashscope_image import DashScopeImageProvider
                return self._get("image", lambda: DashScopeImageProvider(api_key=api_key))
            except ImportError:
                pass
        # Priority 3: HuggingFace Inference API (free, no GPU needed)
        if self._mode in ("hybrid", "huggingface", "hf"):
            try:
                from app.providers.huggingface import HuggingFaceImageProvider
                return self._get("image", lambda: HuggingFaceImageProvider(
                    api_key=getattr(self._settings, "HUGGINGFACE_API_KEY", ""),
                ))
            except ImportError:
                pass
        return self._get("image", MockImageProvider)

    def get_video_provider(self) -> VideoProvider:
        # DashScope cloud Wan (Alibaba Cloud) — highest priority if API key is set
        if self._mode in ("hybrid", "wan", "wan_video", "dashscope", "wan_cloud"):
            api_key = self._settings.WAN_API_KEY or self._settings.DASHSCOPE_API_KEY
            if api_key:
                try:
                    from app.providers.wan_dashscope import DashScopeWanProvider
                    return self._get("video", lambda: DashScopeWanProvider(api_key=api_key))
                except ImportError:
                    pass
        # Self-hosted Wan server
        if self._mode in ("hybrid", "wan", "wan_video"):
            try:
                from app.providers.wan_video import WanVideoProvider
                return self._get("video", lambda: WanVideoProvider(
                    base_url=self._settings.WAN_VIDEO_URL or "http://localhost:8080"
                ))
            except ImportError:
                pass
        return self._get("video", MockVideoProvider)

    def get_voice_provider(self) -> VoiceProvider:
        if self._mode in ("hybrid", "elevenlabs"):
            try:
                from app.providers.elevenlabs import ElevenLabsVoiceProvider
                return self._get("voice", lambda: ElevenLabsVoiceProvider(
                    api_key=self._settings.ELEVENLABS_API_KEY
                ))
            except ImportError:
                pass
        return self._get("voice", MockVoiceProvider)

    def get_trainer_provider(self) -> TrainerProvider:
        # HuggingFace LoRA trainer (runs on MPS/CUDA)
        try:
            from app.providers.hf_trainer import HuggingFaceTrainer
            return self._get("trainer", HuggingFaceTrainer)
        except ImportError:
            pass
        return self._get("trainer", MockTrainerProvider)

    def get_storage_provider(self) -> StorageProvider:
        # MinIO storage when available
        if self._mode in ("hybrid", "minio"):
            try:
                from app.storage import MinIOStorageProvider
                return self._get("storage", MinIOStorageProvider)
            except (ImportError, Exception):
                pass
        # Local filesystem storage (hard drive)
        try:
            from app.providers.filesystem_storage import FileSystemStorageProvider
            return self._get("storage", FileSystemStorageProvider)
        except (ImportError, Exception):
            pass
        return self._get("storage", MockStorageProvider)

    def get_instagram_provider(self):
        """Get Instagram analytics provider (returns None if not configured)."""
        token = self._settings.INSTAGRAM_ACCESS_TOKEN
        account_id = self._settings.INSTAGRAM_ACCOUNT_ID
        if not token or not account_id:
            return None
        try:
            from app.providers.instagram import InstagramProvider
            return InstagramProvider(
                access_token=token,
                instagram_account_id=account_id,
            )
        except ImportError:
            return None

    def health_report(self) -> dict[str, dict]:
        """Get health status of all configured providers.

        Reports based on configuration, not live connectivity pings.
        This avoids blocking the API on slow provider health checks.
        """
        providers_config = {
            "llm": (self.get_llm_provider, self._settings.OLLAMA_URL),
            "image": (self.get_image_provider, self._settings.HUGGINGFACE_API_KEY or self._settings.COMFYUI_URL),
            "video": (self.get_video_provider, self._settings.WAN_API_KEY or self._settings.DASHSCOPE_API_KEY),
            "voice": (self.get_voice_provider, self._settings.ELEVENLABS_API_KEY),
            "trainer": (self.get_trainer_provider, "configured"),
            "storage": (self.get_storage_provider, "./storage/"),
        }
        report = {}
        for name, (getter, config_value) in providers_config.items():
            instance = getter()
            is_mock = "mock" in type(instance).__name__.lower()
            provider_name = getattr(instance, "_provider", type(instance).__name__).replace("mock_", "")
            report[name] = {
                "status": "green" if not is_mock or name in ("storage",) else "yellow",
                "provider": provider_name,
                "details": "Configured" if config_value else "Using defaults",
            }

        # Instagram — optional
        ig = self.get_instagram_provider()
        if ig:
            report["instagram"] = {
                "status": "green",
                "provider": "instagram_graph_api",
                "details": f"Account {self._settings.INSTAGRAM_ACCOUNT_ID[:8]}...",
            }
        else:
            report["instagram"] = {
                "status": "yellow",
                "provider": "instagram_graph_api",
                "details": "Not configured",
            }

        return report


# Singleton registry
_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry


def reset_registry():
    global _registry
    _registry = None
