"""Persona Studio — Provider abstraction layer."""

from app.providers.base import (
    LLMProvider,
    ImageProvider,
    VideoProvider,
    VoiceProvider,
    TrainerProvider,
    StorageProvider,
)
from app.providers.mocks import (
    MockLLMProvider,
    MockImageProvider,
    MockVideoProvider,
    MockVoiceProvider,
    MockTrainerProvider,
    MockStorageProvider,
)

PROVIDER_REGISTRY = {
    "mock": {
        "llm": MockLLMProvider,
        "image": MockImageProvider,
        "video": MockVideoProvider,
        "voice": MockVoiceProvider,
        "trainer": MockTrainerProvider,
        "storage": MockStorageProvider,
    },
}


def get_providers(registry: str = "mock") -> dict:
    """Return provider instances for the given registry."""
    reg = PROVIDER_REGISTRY.get(registry, PROVIDER_REGISTRY["mock"])
    return {name: cls() for name, cls in reg.items()}


__all__ = [
    "LLMProvider", "ImageProvider", "VideoProvider", "VoiceProvider",
    "TrainerProvider", "StorageProvider",
    "MockLLMProvider", "MockImageProvider", "MockVideoProvider",
    "MockVoiceProvider", "MockTrainerProvider", "MockStorageProvider",
    "get_providers",
]
