"""Persona Studio — fake provider tests.

The application has no mock providers; these test the deterministic fakes
in tests/fakes.py, which are the offline stand-ins injected via
force_override() in ENVIRONMENT=test.
"""

import pytest
from tests.fakes import (
    FakeLLMProvider, FakeImageProvider, FakeVideoProvider,
    FakeVoiceProvider, FakeTrainerProvider, FakeStorageProvider,
)


@pytest.mark.asyncio
async def test_storage_upload_download():
    store = FakeStorageProvider()
    result = await store.upload("test/key.png", b"fake png data")
    assert result.success
    result = await store.download("test/key.png")
    assert result.success
    assert result.data["data"] == b"fake png data"


@pytest.mark.asyncio
async def test_storage_not_found():
    store = FakeStorageProvider()
    result = await store.download("nonexistent")
    assert not result.success


@pytest.mark.asyncio
async def test_llm_completion():
    llm = FakeLLMProvider()
    result = await llm.complete(
        system_prompt="You are a helpful assistant.",
        user_prompt="Generate identity candidates for Ava",
    )
    assert result.success
    assert "text" in result.data


@pytest.mark.asyncio
async def test_image_generation():
    provider = FakeImageProvider()
    result = await provider.generate(
        prompt="portrait of a model",
        width=1024, height=1024, seed=42,
    )
    assert result.success
    assert result.data["seed"] == 42
    assert result.data["width"] == 1024
    assert result.data["image_bytes"]


@pytest.mark.asyncio
async def test_image_deterministic():
    provider = FakeImageProvider()
    r1 = await provider.generate(prompt="test", seed=42)
    r2 = await provider.generate(prompt="test", seed=42)
    assert r1.data["image_bytes"] == r2.data["image_bytes"]


@pytest.mark.asyncio
async def test_image_edit():
    provider = FakeImageProvider()
    result = await provider.edit_image(
        reference_image_bytes=b"ref", prompt="same face, new scene", seed=7,
    )
    assert result.success
    assert result.data["image_bytes"]


@pytest.mark.asyncio
async def test_video_generation():
    provider = FakeVideoProvider()
    result = await provider.text_to_video(prompt="lifestyle scene")
    assert result.success
    assert result.data["video_bytes"]


@pytest.mark.asyncio
async def test_voice_create_and_synth():
    voice = FakeVoiceProvider()
    result = await voice.create_voice(name="Ava_voice", accent="South African")
    assert result.success
    voice_id = result.data["voice_id"]

    synth = await voice.synthesize(text="Hello world", voice_id=voice_id)
    assert synth.success
    assert synth.data["audio_bytes"]


@pytest.mark.asyncio
async def test_trainer_train_validate():
    trainer = FakeTrainerProvider()
    result = await trainer.train(dataset_id="ds_001", epochs=5)
    assert result.success
    assert result.data["model_path"]

    validate = await trainer.validate(
        model_path=result.data["model_path"],
        validation_images=["img1.png", "img2.png"],
    )
    assert validate.success
    assert validate.data["passed"] is True


@pytest.mark.asyncio
async def test_all_providers_health_check():
    for Provider in [FakeLLMProvider, FakeImageProvider, FakeVideoProvider,
                     FakeVoiceProvider, FakeTrainerProvider, FakeStorageProvider]:
        p = Provider()
        result = await p.health_check()
        assert result.success


# ── the selector fields must exist ───────────────────────────────────

def test_every_capability_selector_is_a_real_setting():
    """A `<CAPABILITY>_PROVIDER` field that does not exist reports as a red
    capability, not as a config error.

    This happened: `STORAGE_PROVIDER` was typed onto the tail of the previous
    line's `#` comment in config.py, so the class attribute was never created.
    `Settings` does not validate unknown env names into existence the way it
    would a declared field, and the registry reaches its selector through
    `getattr(..., capability.upper() + "_PROVIDER")` — so storage resolved to
    None and `/health` showed `storage  red  None  'Settings' object has no
    attribute 'STORAGE_PROVIDER'`. Nothing at import time objects, a `.env`
    STORAGE_PROVIDER is silently ignored, and the symptom looks like a broken
    backend rather than a typo.

    Pinning the names against `required_capabilities()` means a new capability
    cannot land in the registry without its selector existing here too.
    """
    from app.config import Settings
    from app.providers.registry import ProviderRegistry

    settings = Settings()
    registry = ProviderRegistry()
    assert set(registry.required_capabilities()) == {
        "llm", "image", "video", "voice", "trainer", "storage", "moderation",
    }, "the capability list moved — update this test's expectation deliberately"

    for capability in registry.required_capabilities():
        field = capability.upper() + "_PROVIDER"
        assert field in Settings.model_fields, (
            f"{field} is not a field on Settings — the registry reads it via "
            "getattr() and the capability will report red instead of raising"
        )
        assert getattr(settings, field) != "", (
            f"{field} exists but is empty; the capability cannot resolve"
        )
