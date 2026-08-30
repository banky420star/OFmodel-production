"""Persona Studio — Deterministic mock providers.

These providers allow the complete workflow to run without any external services.
They are CLEARLY identified as mocks. Never present mock results as real.
"""

from __future__ import annotations
import hashlib
import random
import time
import uuid
from typing import Any

from app.providers.base import (
    LLMProvider, ImageProvider, VideoProvider, VoiceProvider,
    TrainerProvider, StorageProvider, ProviderResult,
)


def _mock_id() -> str:
    return uuid.uuid4().hex[:12]


def _fake_png(width: int, height: int, seed: int) -> bytes:
    """Generate a minimal valid PNG (1x1 pixel, deterministic by seed)."""
    import struct
    import zlib

    rng = random.Random(seed)
    r, g, b = rng.randint(100, 255), rng.randint(100, 255), rng.randint(100, 255)

    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + c + crc

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = b"\x00" + bytes([r, g, b])
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return header + ihdr + idat + iend


class MockStorageProvider(StorageProvider):
    """In-memory mock storage provider."""

    def __init__(self):
        self._store: dict[str, bytes] = {}
        self._provider = "mock_storage"

    async def upload(self, key: str, data: bytes, content_type: str = "image/png") -> ProviderResult:
        self._store[key] = data
        return ProviderResult(success=True, data={"key": key, "size": len(data)}, provider=self._provider)

    async def download(self, key: str) -> ProviderResult:
        if key not in self._store:
            return ProviderResult(success=False, error=f"Key not found: {key}", provider=self._provider)
        return ProviderResult(success=True, data={"key": key, "data": self._store[key]}, provider=self._provider)

    async def get_presigned_url(self, key: str, expires: int = 3600) -> str:
        return f"http://mock-storage/{key}?expires={expires}"

    async def list_objects(self, prefix: str) -> ProviderResult:
        keys = [k for k in self._store if k.startswith(prefix)]
        return ProviderResult(success=True, data={"keys": keys}, provider=self._provider)

    async def health_check(self) -> ProviderResult:
        return ProviderResult(success=True, data={"status": "mock"}, provider=self._provider)


class MockLLMProvider(LLMProvider):
    """Deterministic mock LLM that returns structured responses."""

    def __init__(self):
        self._provider = "mock_llm"

    async def complete(
        self, system_prompt: str, user_prompt: str,
        schema: dict | None = None, temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> ProviderResult:
        # Deterministic response based on prompt hash
        seed = int(hashlib.md5(user_prompt.encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)

        # Generate appropriate structured response
        if "identity" in user_prompt.lower() or "candidate" in user_prompt.lower():
            data = self._generate_identity_candidate(rng)
        elif "shoot" in user_prompt.lower() or "plan" in user_prompt.lower():
            data = self._generate_shoot_plan(rng)
        elif "qa" in user_prompt.lower() or "quality" in user_prompt.lower():
            data = self._generate_qa_result(rng)
        elif "caption" in user_prompt.lower():
            data = self._generate_caption(rng)
        elif "forecast" in user_prompt.lower():
            data = self._generate_forecast(rng)
        else:
            data = {"response": "Mock LLM response", "confidence": rng.uniform(0.85, 0.99)}

        return ProviderResult(
            success=True,
            data={"content": data, "tokens_used": rng.randint(100, 800)},
            provider=self._provider,
        )

    def _generate_identity_candidate(self, rng: random.Random) -> dict:
        names = ["Ava", "Luna", "Nova", "Stella", "Iris", "Aria", "Zara", "Mia"]
        hair_styles = ["long blonde", "short brunette", "auburn waves", "platinum pixie"]
        eye_colours = ["blue", "green", "hazel", "grey"]
        return {
            "name": rng.choice(names),
            "age": rng.randint(21, 28),
            "hair": rng.choice(hair_styles),
            "eyes": rng.choice(eye_colours),
            "brand": "luxury lifestyle",
            "personality": rng.sample(["confident", "playful", "elegant", "warm", "bold"], 2),
            "identity_type": "fictional_synthetic",
            "adult": True,
        }

    def _generate_shoot_plan(self, rng: random.Random) -> dict:
        locations = ["modern apartment", "beach resort", "city rooftop", "luxury hotel", "garden terrace"]
        wardrobe = ["casual chic", "evening wear", "resort casual", "athleisure", "business casual"]
        return {
            "concept": f"{rng.choice(['relaxed', 'glamorous', 'casual', 'sophisticated'])} {rng.choice(['sunday', 'evening', 'morning', 'golden hour'])}",
            "objective": "lifestyle content generation",
            "location": rng.choice(locations),
            "lighting": rng.choice(["natural", "golden hour", "studio", "ambient"]),
            "wardrobe": rng.choice(wardrobe),
            "camera_style": rng.choice(["portrait", "lifestyle", "editorial", "candid"]),
            "expected_assets": rng.randint(6, 15),
        }

    def _generate_qa_result(self, rng: random.Random) -> dict:
        score = rng.uniform(0.82, 0.99)
        return {
            "approved": score >= 0.85,
            "identity_score": round(score, 3),
            "quality_score": round(rng.uniform(0.80, 0.98), 3),
            "issues": [] if score >= 0.90 else [{"type": "minor", "detail": "slight variation"}],
        }

    def _generate_caption(self, rng: random.Random) -> dict:
        captions = [
            "Sunday vibes ✨",
            "Golden hour moments ☀️",
            "City lights, city nights 🌃",
            "Living my best life 💫",
        ]
        return {
            "caption": rng.choice(captions),
            "hashtags": ["#lifestyle", "#luxury", "#content"],
        }

    def _generate_forecast(self, rng: random.Random) -> dict:
        return {
            "scenario": "base",
            "monthly_growth_rate": round(rng.uniform(0.05, 0.15), 3),
            "estimated_revenue": round(rng.uniform(5000, 25000), 2),
        }

    async def health_check(self) -> ProviderResult:
        return ProviderResult(success=True, data={"status": "mock"}, provider=self._provider)


class MockImageProvider(ImageProvider):
    """Deterministic mock image generator."""

    def __init__(self):
        self._provider = "mock_image"
        self._storage = MockStorageProvider()

    async def generate(
        self, prompt: str, negative_prompt: str = "",
        width: int = 1024, height: int = 1024,
        steps: int = 30, cfg_scale: float = 7.0,
        seed: int = -1, lora_path: str = "",
        lora_strength: float = 0.8,
    ) -> ProviderResult:
        if seed == -1:
            seed = random.randint(0, 2**31)
        image_data = _fake_png(width, height, seed)
        key = f"mock/images/img_s{seed}_{width}x{height}.png"
        await self._storage.upload(key, image_data)
        return ProviderResult(
            success=True,
            data={
                "image_key": key,
                "seed": seed,
                "width": width,
                "height": height,
                "prompt": prompt,
                "generation_time_ms": random.randint(200, 800),
                "is_mock": True,
            },
            provider=self._provider,
        )

    async def img2img(self, image_key: str, prompt: str, strength: float = 0.75, **kwargs) -> ProviderResult:
        seed = kwargs.get("seed", random.randint(0, 2**31))
        image_data = _fake_png(kwargs.get("width", 1024), kwargs.get("height", 1024), seed)
        key = f"mock/images/{_mock_id()}_s{seed}.png"
        await self._storage.upload(key, image_data)
        return ProviderResult(
            success=True,
            data={"image_key": key, "seed": seed, "source": image_key, "is_mock": True},
            provider=self._provider,
        )

    async def upscale(self, image_key: str, scale: int = 2) -> ProviderResult:
        key = f"mock/images/{_mock_id()}_upscaled.png"
        image_data = _fake_png(1024 * scale, 1024 * scale, hash(image_key) % 2**31)
        await self._storage.upload(key, image_data)
        return ProviderResult(
            success=True,
            data={"image_key": key, "source": image_key, "scale": scale, "is_mock": True},
            provider=self._provider,
        )

    async def health_check(self) -> ProviderResult:
        return ProviderResult(success=True, data={"status": "mock", "gpu": "none"}, provider=self._provider)


class MockVideoProvider(VideoProvider):
    """Deterministic mock video generator."""

    def __init__(self):
        self._provider = "mock_video"
        self._storage = MockStorageProvider()

    async def image_to_video(
        self, image_key: str, prompt: str = "",
        duration: float = 4.0, fps: int = 24,
    ) -> ProviderResult:
        key = f"mock/videos/{_mock_id()}.mp4"
        fake_data = b"\x00" * 1024  # minimal mock video bytes
        await self._storage.upload(key, fake_data)
        return ProviderResult(
            success=True,
            data={
                "video_key": key,
                "source_image": image_key,
                "duration": duration,
                "fps": fps,
                "generation_time_ms": random.randint(1000, 5000),
                "is_mock": True,
            },
            provider=self._provider,
        )

    async def text_to_video(
        self, prompt: str, duration: float = 4.0,
        width: int = 1024, height: int = 576,
    ) -> ProviderResult:
        key = f"mock/videos/{_mock_id()}.mp4"
        fake_data = b"\x00" * 1024
        await self._storage.upload(key, fake_data)
        return ProviderResult(
            success=True,
            data={
                "video_key": key,
                "duration": duration,
                "width": width,
                "height": height,
                "prompt": prompt,
                "generation_time_ms": random.randint(1000, 5000),
                "is_mock": True,
            },
            provider=self._provider,
        )

    async def health_check(self) -> ProviderResult:
        return ProviderResult(success=True, data={"status": "mock"}, provider=self._provider)


class MockVoiceProvider(VoiceProvider):
    """Deterministic mock voice provider."""

    def __init__(self):
        self._provider = "mock_voice"
        self._voices: dict[str, dict] = {}
        self._storage = MockStorageProvider()

    async def create_voice(
        self, name: str, description: str = "",
        accent: str = "", tone: str = "",
    ) -> ProviderResult:
        voice_id = f"mock_voice_{_mock_id()}"
        self._voices[voice_id] = {
            "name": name,
            "description": description,
            "accent": accent,
            "tone": tone,
        }
        return ProviderResult(
            success=True,
            data={"voice_id": voice_id, "name": name, "is_mock": True},
            provider=self._provider,
        )

    async def synthesize(
        self, text: str, voice_id: str, speed: float = 1.0,
        output_format: str = "wav",
    ) -> ProviderResult:
        key = f"mock/audio/{_mock_id()}.{output_format}"
        fake_data = b"\x00" * 2048
        await self._storage.upload(key, fake_data)
        duration = len(text) * 0.05 / speed  # rough estimate
        return ProviderResult(
            success=True,
            data={
                "voice_key": key,
                "voice_id": voice_id,
                "duration_seconds": round(duration, 2),
                "format": output_format,
                "sample_rate": 22050,
                "generation_time_ms": random.randint(200, 1000),
                "is_mock": True,
            },
            provider=self._provider,
        )

    async def health_check(self) -> ProviderResult:
        return ProviderResult(success=True, data={"status": "mock"}, provider=self._provider)


class MockTrainerProvider(TrainerProvider):
    """Deterministic mock LoRA trainer."""

    def __init__(self):
        self._provider = "mock_trainer"

    async def train(
        self, dataset_id: str, model_type: str = "lora",
        rank: int = 16, epochs: int = 10,
        learning_rate: float = 1e-4, batch_size: int = 4,
    ) -> ProviderResult:
        return ProviderResult(
            success=True,
            data={
                "model_path": f"mock/models/{_mock_id()}/lora.safetensors",
                "model_type": model_type,
                "rank": rank,
                "epochs_completed": epochs,
                "final_loss": round(random.uniform(0.01, 0.05), 4),
                "training_time_s": round(random.uniform(10, 60), 1),
                "is_mock": True,
            },
            provider=self._provider,
        )

    async def validate(self, model_path: str, validation_images: list[str]) -> ProviderResult:
        score = round(random.uniform(0.85, 0.99), 3)
        return ProviderResult(
            success=True,
            data={
                "validation_score": score,
                "identity_similarity": score,
                "quality_score": round(random.uniform(0.80, 0.95), 3),
                "images_validated": len(validation_images),
                "passed": score >= 0.85,
                "is_mock": True,
            },
            provider=self._provider,
        )

    async def health_check(self) -> ProviderResult:
        return ProviderResult(success=True, data={"status": "mock", "gpu": "none"}, provider=self._provider)
