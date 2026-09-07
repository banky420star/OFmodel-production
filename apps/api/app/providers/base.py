"""Persona Studio — Abstract provider interfaces."""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4


@dataclass
class ProviderResult:
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    provider: str = ""
    latency_ms: float = 0


class LLMProvider(ABC):
    """Structured LLM decision-making interface."""

    @abstractmethod
    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> ProviderResult:
        ...

    @abstractmethod
    async def health_check(self) -> ProviderResult:
        ...


class ImageProvider(ABC):
    """Image generation interface."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        steps: int = 30,
        cfg_scale: float = 7.0,
        seed: int = -1,
        lora_path: str = "",
        lora_strength: float = 0.8,
    ) -> ProviderResult:
        ...

    @abstractmethod
    async def img2img(
        self,
        image_key: str,
        prompt: str,
        strength: float = 0.75,
        **kwargs,
    ) -> ProviderResult:
        ...

    @abstractmethod
    async def upscale(self, image_key: str, scale: int = 2) -> ProviderResult:
        ...

    @abstractmethod
    async def health_check(self) -> ProviderResult:
        ...


class VideoProvider(ABC):
    """Video generation interface."""

    @abstractmethod
    async def image_to_video(
        self,
        image_key: str,
        prompt: str = "",
        duration: float = 15.0,
        fps: int = 24,
    ) -> ProviderResult:
        ...

    @abstractmethod
    async def text_to_video(
        self,
        prompt: str,
        duration: float = 20.0,
        width: int = 1024,
        height: int = 576,
    ) -> ProviderResult:
        ...

    @abstractmethod
    async def health_check(self) -> ProviderResult:
        ...


class VoiceProvider(ABC):
    """Voice synthesis interface."""

    @abstractmethod
    async def create_voice(
        self,
        name: str,
        description: str = "",
        accent: str = "",
        tone: str = "",
    ) -> ProviderResult:
        ...

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice_id: str,
        speed: float = 1.0,
        output_format: str = "wav",
    ) -> ProviderResult:
        ...

    @abstractmethod
    async def health_check(self) -> ProviderResult:
        ...


class TrainerProvider(ABC):
    """LoRA / model training interface."""

    @abstractmethod
    async def train(
        self,
        dataset_id: str,
        model_type: str = "lora",
        rank: int = 16,
        epochs: int = 10,
        learning_rate: float = 1e-4,
        batch_size: int = 4,
    ) -> ProviderResult:
        ...

    @abstractmethod
    async def validate(
        self,
        model_path: str,
        validation_images: list[str],
    ) -> ProviderResult:
        ...

    @abstractmethod
    async def health_check(self) -> ProviderResult:
        ...


class StorageProvider(ABC):
    """Object storage interface (MinIO / S3)."""

    @abstractmethod
    async def upload(self, key: str, data: bytes, content_type: str = "image/png") -> ProviderResult:
        ...

    @abstractmethod
    async def download(self, key: str) -> ProviderResult:
        ...

    @abstractmethod
    async def get_presigned_url(self, key: str, expires: int = 3600) -> str:
        ...

    @abstractmethod
    async def list_objects(self, prefix: str) -> ProviderResult:
        ...

    @abstractmethod
    async def health_check(self) -> ProviderResult:
        ...
