"""Test fakes for the provider registry — live in tests/, never in app/.

These are injected via `registry.force_override()` in tests
(ENVIRONMENT=test only). They are deterministic in-process fakes so the
28-test suite runs offline; the application itself has no mock providers.
"""

from __future__ import annotations

import zlib

from app.providers.base import (
    ImageProvider, LLMProvider, ProviderResult, StorageProvider,
    TrainerProvider, VideoProvider, VoiceProvider,
)


def _tiny_png(seed: int, size: int = 64) -> bytes:
    """Deterministic real PNG — must be decodable by PIL (the identity
    engine opens avatar files as reference images)."""
    import io

    from PIL import Image

    hue = zlib.crc32(str(seed).encode()) % 256
    img = Image.new("RGB", (8, 8), (hue, (hue * 3) % 256, (hue * 7) % 256))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeLLMProvider(LLMProvider):
    """Returns canned structured JSON per prompt keyword."""

    def __init__(self, reply: str = '{"candidates": []}'):
        self.reply = reply

    async def complete(self, system_prompt, user_prompt, schema=None,
                       temperature=0.7, max_tokens=2048) -> ProviderResult:
        return ProviderResult(True, {"text": self.reply}, provider="fake_llm", latency_ms=1)

    async def health_check(self) -> ProviderResult:
        return ProviderResult(True, {"ok": True}, provider="fake_llm")


class FakeImageProvider(ImageProvider):
    """Writes a deterministic tiny PNG per call."""

    async def generate(self, prompt, negative_prompt="", width=1024, height=1024,
                       steps=30, cfg_scale=7.0, seed=-1, lora_path="",
                       lora_strength=0.8, session_id="") -> ProviderResult:
        return ProviderResult(
            True, {"image_bytes": _tiny_png(seed or 1), "width": width,
                   "height": height, "seed": seed},
            provider="fake_image", latency_ms=1,
        )

    async def img2img(self, image_key, prompt, strength=0.75, **kwargs) -> ProviderResult:
        return ProviderResult(True, {"image_bytes": _tiny_png(2)}, provider="fake_image")

    async def upscale(self, image_key, scale=2) -> ProviderResult:
        return ProviderResult(True, {"image_bytes": _tiny_png(3)}, provider="fake_image")

    async def edit_image(self, reference_image_bytes, prompt, negative_prompt="",
                         width=1024, height=1024, seed=-1) -> ProviderResult:
        return ProviderResult(
            True, {"image_bytes": _tiny_png(seed or 4), "width": width,
                   "height": height, "seed": seed},
            provider="fake_image_edit", latency_ms=1,
        )

    async def health_check(self) -> ProviderResult:
        return ProviderResult(True, {"ok": True}, provider="fake_image")


class FakeVideoProvider(VideoProvider):
    async def image_to_video(self, image_key, prompt="", duration=15.0, fps=24) -> ProviderResult:
        return ProviderResult(True, {"video_bytes": b"fake-mp4", "duration": duration},
                              provider="fake_video", latency_ms=1)

    async def text_to_video(self, prompt, duration=20.0, width=1024, height=576) -> ProviderResult:
        return ProviderResult(True, {"video_bytes": b"fake-mp4", "duration": duration},
                              provider="fake_video", latency_ms=1)

    async def health_check(self) -> ProviderResult:
        return ProviderResult(True, {"ok": True}, provider="fake_video")


class FakeVoiceProvider(VoiceProvider):
    async def create_voice(self, name, description="", accent="", tone="") -> ProviderResult:
        return ProviderResult(True, {"voice_id": "fake_voice_1"}, provider="fake_voice", latency_ms=1)

    async def synthesize(self, text, voice_id, speed=1.0, output_format="wav") -> ProviderResult:
        return ProviderResult(True, {"audio_bytes": b"fake-wav"}, provider="fake_voice", latency_ms=1)

    async def health_check(self) -> ProviderResult:
        return ProviderResult(True, {"ok": True}, provider="fake_voice")


class FakeTrainerProvider(TrainerProvider):
    async def train(self, dataset_id, model_type="lora", rank=16, epochs=10,
                    learning_rate=1e-4, batch_size=4) -> ProviderResult:
        return ProviderResult(
            True,
            {"model_path": f"/tmp/fake_lora_{dataset_id}.safetensors", "consistency_score": 0.95},
            provider="fake_trainer", latency_ms=1,
        )

    async def validate(self, model_path, validation_images) -> ProviderResult:
        return ProviderResult(True, {"score": 0.95, "passed": True},
                              provider="fake_trainer", latency_ms=1)

    async def health_check(self) -> ProviderResult:
        return ProviderResult(True, {"ok": True}, provider="fake_trainer")


class FakeStorageProvider(StorageProvider):
    def __init__(self):
        self._objects: dict[str, bytes] = {}

    async def upload(self, key, data, content_type="image/png") -> ProviderResult:
        self._objects[key] = data
        return ProviderResult(True, {"key": key, "size": len(data)}, provider="fake_storage")

    async def download(self, key) -> ProviderResult:
        data = self._objects.get(key)
        return ProviderResult(data is not None, {"data": data}, provider="fake_storage")

    async def get_presigned_url(self, key, expires=3600) -> str:
        return f"fake://{key}"

    async def list_objects(self, prefix) -> ProviderResult:
        keys = [k for k in self._objects if k.startswith(prefix)]
        return ProviderResult(True, {"keys": keys}, provider="fake_storage")

    async def health_check(self) -> ProviderResult:
        return ProviderResult(True, {"ok": True}, provider="fake_storage")