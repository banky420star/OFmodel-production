"""Persona Studio — Provider Registry.

Dynamically selects mock or real providers based on configuration.
This is the central place where provider swapping happens.

Environment variables:
  PROVIDER_REGISTRY = mock | comfyui | hybrid
  COMFYUI_URL = http://localhost:8188
  ELEVENLABS_API_KEY = sk_...
  WAN_VIDEO_URL = http://localhost:8080
  OLLAMA_URL = http://localhost:11434
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
        import os
        self._mode = mode or os.getenv("PROVIDER_REGISTRY", "mock")
        self._instances: dict[str, object] = {}

    def _get(self, key: str, factory):
        if key not in self._instances:
            self._instances[key] = factory()
        return self._instances[key]

    def get_llm_provider(self) -> LLMProvider:
        if self._mode in ("hybrid", "ollama", "openai"):
            try:
                from app.providers.ollama_provider import OllamaLLMProvider
                return self._get("llm", OllamaLLMProvider)
            except ImportError:
                pass
        return self._get("llm", MockLLMProvider)

    def get_image_provider(self) -> ImageProvider:
        if self._mode in ("hybrid", "comfyui"):
            try:
                from app.providers.comfyui import ComfyUIImageProvider
                return self._get("image", lambda: ComfyUIImageProvider(
                    base_url=os.getenv("COMFYUI_URL", "http://localhost:8188")
                ))
            except ImportError:
                pass
        return self._get("image", MockImageProvider)

    def get_video_provider(self) -> VideoProvider:
        if self._mode in ("hybrid", "wan", "wan_video"):
            try:
                from app.providers.wan_video import WanVideoProvider
                return self._get("video", lambda: WanVideoProvider(
                    base_url=os.getenv("WAN_VIDEO_URL", "http://localhost:8080")
                ))
            except ImportError:
                pass
        return self._get("video", MockVideoProvider)

    def get_voice_provider(self) -> VoiceProvider:
        if self._mode in ("hybrid", "elevenlabs"):
            try:
                from app.providers.elevenlabs import ElevenLabsVoiceProvider
                return self._get("voice", lambda: ElevenLabsVoiceProvider(
                    api_key=os.getenv("ELEVENLABS_API_KEY", "")
                ))
            except ImportError:
                pass
        return self._get("voice", MockVoiceProvider)

    def get_trainer_provider(self) -> TrainerProvider:
        # Real trainer requires GPU worker — always mock for now
        return self._get("trainer", MockTrainerProvider)

    def get_storage_provider(self) -> StorageProvider:
        # MinIO storage when available, otherwise in-memory mock
        if self._mode in ("hybrid", "minio"):
            try:
                from app.storage import MinIOStorageProvider
                return self._get("storage", MinIOStorageProvider)
            except (ImportError, Exception):
                pass
        return self._get("storage", MockStorageProvider)

    def health_report(self) -> dict[str, dict]:
        """Get health status of all configured providers."""
        providers = {
            "llm": self.get_llm_provider(),
            "image": self.get_image_provider(),
            "video": self.get_video_provider(),
            "voice": self.get_voice_provider(),
            "trainer": self.get_trainer_provider(),
            "storage": self.get_storage_provider(),
        }
        import asyncio
        report = {}
        for name, provider in providers.items():
            try:
                result = asyncio.get_event_loop().run_until_complete(provider.health_check())
                report[name] = {
                    "status": "green" if result.success else "yellow",
                    "provider": result.provider,
                    "details": result.data,
                }
            except Exception as e:
                report[name] = {"status": "red", "error": str(e)}
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
