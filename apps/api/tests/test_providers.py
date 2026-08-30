"""Persona Studio — Mock provider tests."""

import pytest
from app.providers.mocks import (
    MockLLMProvider, MockImageProvider, MockVideoProvider,
    MockVoiceProvider, MockTrainerProvider, MockStorageProvider,
)


@pytest.mark.asyncio
async def test_storage_upload_download():
    store = MockStorageProvider()
    result = await store.upload("test/key.png", b"fake png data")
    assert result.success
    result = await store.download("test/key.png")
    assert result.success
    assert result.data["data"] == b"fake png data"


@pytest.mark.asyncio
async def test_storage_not_found():
    store = MockStorageProvider()
    result = await store.download("nonexistent")
    assert not result.success


@pytest.mark.asyncio
async def test_llm_completion():
    llm = MockLLMProvider()
    result = await llm.complete(
        system_prompt="You are a helpful assistant.",
        user_prompt="Generate identity candidates for Ava",
    )
    assert result.success
    assert "content" in result.data


@pytest.mark.asyncio
async def test_image_generation():
    provider = MockImageProvider()
    result = await provider.generate(
        prompt="portrait of a model",
        width=1024, height=1024, seed=42,
    )
    assert result.success
    assert result.data["seed"] == 42
    assert result.data["width"] == 1024
    assert result.data["is_mock"] is True


@pytest.mark.asyncio
async def test_image_deterministic():
    provider = MockImageProvider()
    r1 = await provider.generate(prompt="test", seed=42)
    r2 = await provider.generate(prompt="test", seed=42)
    assert r1.data["image_key"] == r2.data["image_key"]


@pytest.mark.asyncio
async def test_video_generation():
    provider = MockVideoProvider()
    result = await provider.text_to_video(prompt="lifestyle scene")
    assert result.success
    assert result.data["is_mock"] is True


@pytest.mark.asyncio
async def test_voice_create_and_synth():
    voice = MockVoiceProvider()
    result = await voice.create_voice(name="Ava_voice", accent="South African")
    assert result.success
    voice_id = result.data["voice_id"]

    synth = await voice.synthesize(text="Hello world", voice_id=voice_id)
    assert synth.success
    assert synth.data["voice_key"] != ""


@pytest.mark.asyncio
async def test_trainer_train_validate():
    trainer = MockTrainerProvider()
    result = await trainer.train(dataset_id="ds_001", epochs=5)
    assert result.success
    assert result.data["epochs_completed"] == 5

    validate = await trainer.validate(
        model_path=result.data["model_path"],
        validation_images=["img1.png", "img2.png"],
    )
    assert validate.success
    assert 0.0 <= validate.data["validation_score"] <= 1.0


@pytest.mark.asyncio
async def test_all_providers_health_check():
    for Provider in [MockLLMProvider, MockImageProvider, MockVideoProvider,
                     MockVoiceProvider, MockTrainerProvider, MockStorageProvider]:
        p = Provider()
        result = await p.health_check()
        assert result.success
