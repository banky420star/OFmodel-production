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