"""Persona Studio — Mock provider tests."""

import asyncio

import pytest
from app.providers.base import ProviderResult
from app.providers.mocks import (
    MockLLMProvider, MockImageProvider, MockVideoProvider,
    MockVoiceProvider, MockTrainerProvider, MockStorageProvider,
)
from app.providers.registry import ChainImageProvider, ProviderRegistry


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


# ─── Image provider chain (registry) — quota cooldown & fallback ────────

class StubImageProvider:
    """Minimal image provider stub: fails the first `fail_times` calls."""

    def __init__(self, name: str, error: str = "", fail_times: int = 0):
        self.name = name
        self.error = error
        self.fail_times = fail_times
        self.calls = 0

    async def generate(self, prompt="", **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            return ProviderResult(success=False, error=self.error, provider=self.name)
        return ProviderResult(
            success=True, data={"image_key": f"{self.name}.png"}, provider=self.name
        )


@pytest.mark.asyncio
async def test_image_chain_falls_through_on_quota_error():
    quota = StubImageProvider("quota_holder", error="HTTP 429 quota exhausted", fail_times=999)
    backup = StubImageProvider("backup")
    chain = ChainImageProvider([("quota_holder", quota), ("backup", backup)])
    result = await chain.generate(prompt="portrait")
    assert result.success
    assert result.provider == "backup"
    assert quota.calls == 1
    assert backup.calls == 1


@pytest.mark.asyncio
async def test_image_chain_cooldown_skips_failed_provider():
    flaky = StubImageProvider("flaky", error="rate limit exceeded", fail_times=1)
    backup = StubImageProvider("backup")
    chain = ChainImageProvider([("flaky", flaky), ("backup", backup)], cooldown_seconds=900)

    r1 = await chain.generate(prompt="p")  # flaky fails -> backup
    r2 = await chain.generate(prompt="p")  # flaky on cooldown -> backup only

    assert r1.success and r2.success
    assert r2.provider == "backup"
    assert flaky.calls == 1      # skipped while on cooldown
    assert backup.calls == 2


@pytest.mark.asyncio
async def test_image_chain_cooldown_expires_and_retries():
    flaky = StubImageProvider("flaky", error="rate limit exceeded", fail_times=1)
    chain = ChainImageProvider([("flaky", flaky)], cooldown_seconds=0.05)

    r1 = await chain.generate(prompt="p")
    assert not r1.success

    await asyncio.sleep(0.15)    # cooldown TTL elapsed
    r2 = await chain.generate(prompt="p")
    assert r2.success
    assert flaky.calls == 2      # provider recovered and was retried


@pytest.mark.asyncio
async def test_image_chain_non_quota_error_not_blacklisted():
    flaky = StubImageProvider("flaky", error="invalid prompt rejected", fail_times=1)
    backup = StubImageProvider("backup")
    chain = ChainImageProvider([("flaky", flaky), ("backup", backup)])

    await chain.generate(prompt="p")
    await chain.generate(prompt="p")

    # Generic failures don't trigger cooldown — provider is retried each time
    assert flaky.calls == 2
    assert backup.calls == 1  # only the first call fell through to backup


def test_registry_image_chain_order_pollinations_before_mock():
    registry = ProviderRegistry(mode="mock")
    provider = registry.get_image_provider()
    assert isinstance(provider, ChainImageProvider)
    names = provider.provider_names
    assert names[-1] == "mock"                       # Mock is always last
    assert names[-2] == "pollinations"               # Pollinations just before Mock


def test_registry_caches_image_chain_instance():
    registry = ProviderRegistry(mode="mock")
    assert registry.get_image_provider() is registry.get_image_provider()


@pytest.mark.asyncio
async def test_image_chain_auth_error_triggers_cooldown():
    bad_key = StubImageProvider("badkey", error="Auth failed (401)", fail_times=999)
    backup = StubImageProvider("backup")
    chain = ChainImageProvider([("badkey", bad_key), ("backup", backup)])

    r1 = await chain.generate(prompt="p")
    r2 = await chain.generate(prompt="p")

    assert r1.success and r2.success
    assert bad_key.calls == 1          # 401 → cooldown, skipped afterwards
    assert backup.calls == 2


@pytest.mark.asyncio
async def test_image_chain_attempt_timeout_moves_to_next_provider():
    class SlowProvider:
        def __init__(self):
            self.name = "slow"
            self.calls = 0

        async def generate(self, prompt="", **kwargs):
            self.calls += 1
            await asyncio.sleep(5)
            return ProviderResult(success=True, data={}, provider=self.name)

    slow = SlowProvider()
    backup = StubImageProvider("backup")
    chain = ChainImageProvider([("slow", slow), ("backup", backup)], attempt_timeout=0.05)

    result = await chain.generate(prompt="p")

    assert result.success
    assert result.provider == "backup"  # slow provider was cut off by the budget
    assert slow.calls == 1


@pytest.mark.asyncio
async def test_image_chain_edit_image_fails_explicitly_without_edit_support():
    text_only = StubImageProvider("text_only")
    chain = ChainImageProvider([("text_only", text_only)])

    result = await chain.edit_image(b"reference-bytes", prompt="p")

    assert not result.success
    assert "reference image was NOT applied" in result.error
    assert text_only.calls == 0         # never silently regenerated from text


@pytest.mark.asyncio
async def test_image_chain_edit_image_uses_edit_capable_provider():
    class EditCapable(StubImageProvider):
        async def edit_image(self, reference_image_bytes, prompt, **kwargs):
            return ProviderResult(success=True, data={"edited": True}, provider=self.name)

    plain = StubImageProvider("plain")
    editor = EditCapable("editor")
    chain = ChainImageProvider([("plain", plain), ("editor", editor)])

    result = await chain.edit_image(b"reference-bytes", prompt="p")

    assert result.success
    assert result.provider == "editor"


def test_unavailable_error_markers():
    from app.providers.registry import _is_unavailable_error
    # Quota / rate / status codes
    assert _is_unavailable_error("Pollinations returned HTTP 429, 1023 bytes")
    assert _is_unavailable_error("dashscope quota exhausted")
    assert _is_unavailable_error("Auth failed (401) on endpoint")
    assert _is_unavailable_error("403 Forbidden")
    assert _is_unavailable_error("provider timed out after 120s")
    assert _is_unavailable_error("did not complete within 120s")
    assert _is_unavailable_error("Cannot connect to host")
    # Word boundary: a 500 body mentioning "4290 bytes" is NOT a 429
    assert not _is_unavailable_error("HTTP 500: response contained 4290 bytes")
    # Permanent app errors don't trigger cooldown
    assert not _is_unavailable_error("invalid prompt rejected")
    assert not _is_unavailable_error("connection string malformed")
    assert not _is_unavailable_error("invalid image size requested")


def test_registry_skips_huggingface_without_api_key():
    registry = ProviderRegistry(mode="hybrid")
    names = registry.get_image_provider().provider_names
    assert "huggingface" not in names   # no key -> no wasted anonymous 401
    assert names == ["pollinations", "mock"]


def test_health_report_image_not_green_in_mock_mode():
    registry = ProviderRegistry(mode="mock")
    report = registry.health_report()
    assert report["image"]["status"] != "green"


def test_identity_engine_placeholder_png_when_no_image_bytes():
    """Mock chain results carry no image_bytes — placeholder must kick in."""
    from app.identity_engine import _placeholder_png

    p1 = _placeholder_png(42, 1024, 1024)
    p2 = _placeholder_png(42, 1024, 1024)
    assert p1 == p2                       # deterministic per seed
    assert len(p1) > 100
    assert p1[:8] == b"\x89PNG\r\n\x1a\n"  # valid PNG
