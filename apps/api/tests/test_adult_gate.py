"""Persona Studio — adult gate tests.

Three layers have to agree before adult content is produced:
  1. ADULT_CONTENT_ENABLED (global kill-switch, .env)
  2. persona.adult_verified + persona.synthetic_identity (per-persona opt-in)
  3. an adult-capable image provider + fail-closed moderation

They used to disagree: the persona column defaulted to True, so layer 2 passed
for every persona automatically, while layer 3 refused a provider that was
perfectly capable (the check was `IMAGE_PROVIDER != "eachsense"`). These tests
pin the agreement.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models import Persona, PersonaStatus, persona_ready_for_production
from app.providers.base import ImageProvider, ProviderResult
from app.providers.gates import require_adult_image


class _NotAdultProvider(ImageProvider):
    """A real image provider that does not declare adult support."""

    name = "not_adult"

    async def generate(self, prompt: str, **kwargs) -> ProviderResult:
        return ProviderResult(success=False, error="not implemented")

    async def img2img(self, image_key: str, prompt: str, **kwargs) -> ProviderResult:
        return ProviderResult(success=False, error="not implemented")

    async def upscale(self, image_key: str, scale: int = 2) -> ProviderResult:
        return ProviderResult(success=False, error="not implemented")

    async def health_check(self) -> ProviderResult:
        return ProviderResult(success=True, data={})


class _AdultProvider(_NotAdultProvider):
    name = "adult"
    SUPPORTS_ADULT = True


# ── Layer 2: per-persona opt-in ──────────────────────────────────────

def test_persona_not_adult_verified_is_not_ready():
    persona = Persona(name="Test", age=24, adult_verified=False, synthetic_identity=True)
    ok, reason = persona_ready_for_production(persona)
    assert ok is False
    assert "adult-verified" in reason


def test_persona_not_synthetic_is_not_ready():
    persona = Persona(name="Test", age=24, adult_verified=True, synthetic_identity=False)
    ok, reason = persona_ready_for_production(persona)
    assert ok is False
    assert "synthetic" in reason


def test_persona_ready_when_both_are_true():
    persona = Persona(name="Test", age=24, adult_verified=True, synthetic_identity=True)
    ok, reason = persona_ready_for_production(persona)
    assert ok is True
    assert reason == ""


@pytest.mark.asyncio
async def test_persona_defaults_to_not_adult_verified(db):
    """The column default must not claim a verification nobody asked for.

    Asserted after a flush, because a column default is applied at INSERT, not
    at object construction — checking the attribute on an unflushed instance
    would read None and pass for the wrong reason. This is the assertion that
    would have caught the original bug at the model layer: a persona persisted
    with no adult_verified in sight has to be refused by the gate.
    """
    persona = Persona(name="DefaultAdultCheck", age=24, status=PersonaStatus.DRAFT)
    db.add(persona)
    await db.flush()
    await db.refresh(persona)
    assert persona.adult_verified is False
    ok, _ = persona_ready_for_production(persona)
    assert ok is False


# ── Layer 3: provider capability ─────────────────────────────────────

def test_adult_gate_refuses_provider_without_capability(registry_override):
    registry_override("image", _NotAdultProvider())
    registry_override("moderation", object())
    with pytest.raises(HTTPException) as exc:
        require_adult_image()
    assert exc.value.status_code == 409
    # The message has to say which knob to turn, and it must describe the
    # provider actually configured rather than naming a vendor to go buy.
    assert "does not declare adult support" in exc.value.detail
    assert "COMFYUI_ADULT_CHECKPOINT" in exc.value.detail


def test_adult_gate_passes_adult_provider(registry_override):
    image = _AdultProvider()
    registry_override("image", image)
    registry_override("moderation", object())
    resolved = require_adult_image()
    assert resolved["image"] is image


def test_base_provider_does_not_declare_adult_support():
    """Nothing declares adult support by accident — it is opt-in per class."""
    assert ImageProvider.SUPPORTS_ADULT is False


def test_comfyui_adult_support_follows_checkpoint_setting(monkeypatch):
    """A local backend is adult-capable only when the operator says the weights are.

    ComfyUI merely being able to load an adult checkpoint is not the same as one
    being loaded, so the flag is configuration, not capability.
    """
    from app.config import get_settings
    from app.providers.comfyui import ComfyUIImageProvider

    provider = ComfyUIImageProvider()
    settings = get_settings()

    monkeypatch.setattr(settings, "COMFYUI_ADULT_CHECKPOINT", False, raising=False)
    assert provider.SUPPORTS_ADULT is False

    monkeypatch.setattr(settings, "COMFYUI_ADULT_CHECKPOINT", True, raising=False)
    assert provider.SUPPORTS_ADULT is True


# ── Layer 1: global kill-switch ──────────────────────────────────────

def test_kill_switch_blocks_even_with_adult_provider(monkeypatch, registry_override):
    """The kill-switch is checked first and cannot be satisfied by a provider."""
    from app.config import get_settings
    from app.routes.content import _require_adult_allowed

    registry_override("image", _AdultProvider())
    registry_override("moderation", object())
    monkeypatch.setattr(get_settings(), "ADULT_CONTENT_ENABLED", False, raising=False)

    with pytest.raises(HTTPException) as exc:
        _require_adult_allowed()
    assert exc.value.status_code == 403
    assert "ADULT_CONTENT_ENABLED" in exc.value.detail


def test_kill_switch_on_and_adult_provider_allows(monkeypatch, registry_override):
    from app.config import get_settings
    from app.routes.content import _require_adult_allowed

    registry_override("image", _AdultProvider())
    registry_override("moderation", object())
    monkeypatch.setattr(get_settings(), "ADULT_CONTENT_ENABLED", True, raising=False)

    _require_adult_allowed()  # must not raise


# ── The gate's own report ────────────────────────────────────────────

def test_gate_status_names_every_blocker_when_nothing_is_enabled(monkeypatch, registry_override):
    """The operator should be able to read what is missing, not infer it.

    `COMFYUI_ADULT_CHECKPOINT` is a *declaration* that an adult checkpoint is
    loaded — nothing in the app verifies it, so a status report that only said
    "adult: off" would hide the more interesting failure: a flag set true over a
    stock checkpoint. The report therefore names both layers and the checkpoint.
    """
    from app.config import get_settings
    from app.providers.gates import adult_gate_status

    registry_override("image", _NotAdultProvider())
    monkeypatch.setattr(get_settings(), "ADULT_CONTENT_ENABLED", False, raising=False)

    status = adult_gate_status()

    assert status["allowed"] is False
    assert status["adult_content_enabled"] is False
    assert status["image_provider_supports_adult"] is False
    joined = " ".join(status["blockers"])
    assert "ADULT_CONTENT_ENABLED" in joined
    assert "does not declare adult support" in joined


def test_gate_status_does_not_say_allowed_when_only_the_switch_is_on(
    monkeypatch, registry_override
):
    """The layer an operator is most likely to forget: flipping the kill switch
    without an adult-capable provider leaves the gate shut."""
    from app.config import get_settings
    from app.providers.gates import adult_gate_status

    registry_override("image", _NotAdultProvider())
    monkeypatch.setattr(get_settings(), "ADULT_CONTENT_ENABLED", True, raising=False)

    status = adult_gate_status()
    assert status["adult_content_enabled"] is True
    assert status["allowed"] is False, "one layer on is not the gate open"


def test_gate_status_reports_open_only_when_both_layers_agree(monkeypatch, registry_override):
    from app.config import get_settings
    from app.providers.gates import adult_gate_status

    registry_override("image", _AdultProvider())
    monkeypatch.setattr(get_settings(), "ADULT_CONTENT_ENABLED", True, raising=False)

    status = adult_gate_status()
    assert status["allowed"] is True
    assert status["blockers"] == []


def test_gate_status_never_raises_with_no_image_provider(monkeypatch):
    """It is called from a status endpoint, so it must report, not fail.

    With no image provider configured there is nothing to ask, and the honest
    answer is that adult support is not declared — not a 503 out of a health
    page. Patched at the registry lookup rather than by mutating the shared
    registry, which would leak into every later test in the session.
    """
    import app.providers.gates as gates_module
    from app.providers.gates import adult_gate_status

    class _EmptyRegistry:
        def resolve_optional(self, capability):
            return None

    monkeypatch.setattr(gates_module, "get_registry", lambda: _EmptyRegistry())

    status = adult_gate_status()
    assert status["image_provider_supports_adult"] is False
    assert status["allowed"] is False
    assert any("does not declare adult support" in b for b in status["blockers"])
