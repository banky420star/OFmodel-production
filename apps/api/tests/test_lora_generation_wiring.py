"""Persona Studio — the trained LoRA must actually reach generation.

Two independent defects met here. The trainer wrote the adapter into the
project's own storage directory, which ComfyUI's LoraLoader cannot resolve — so
even a successful training produced something generation could not load. And no
generation path passed `lora_path` at all, so the adapter was unused even when
it was in the right place. The identity-locked path goes through `edit_image`
(img2img), not `generate`, so wiring only txt2img would still have left it
unused in practice.

The graph-rewiring test exists because the previous injection code pointed the
sampler's `positive` input at the LoraLoader's *CLIP* output. That is a type
mismatch — the sampler wants conditioning from a CLIPTextEncode — so ComfyUI
rejected every LoRA workflow before running it. A rewiring that looks plausible
and never produces an image is exactly the failure this file has to catch.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.models import Identity, IdentityLock, IdentityStatus, Persona
from app.providers.base import ImageProvider, ProviderResult
from app.providers.comfyui import ComfyUIImageProvider


# ── graph rewiring ───────────────────────────────────────────────────

def _txt2img_graph() -> dict:
    return {
        "3": {"class_type": "KSampler", "inputs": {
            "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
            "latent_image": ["5", 0],
        }},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x.safetensors"}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": "a"}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": "b"}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    }


def test_inject_lora_rewires_model_and_clip():
    graph = _txt2img_graph()
    ComfyUIImageProvider._inject_lora(graph, "persona_x.safetensors", 0.8)

    node = graph["10"]
    assert node["class_type"] == "LoraLoader"
    assert node["inputs"]["lora_name"] == "persona_x.safetensors"
    assert node["inputs"]["strength_model"] == 0.8
    assert node["inputs"]["strength_clip"] == 0.8

    # The sampler's model now comes from the loader...
    assert graph["3"]["inputs"]["model"] == ["10", 0]
    # ...and both text encoders take the LoRA-adjusted CLIP.
    assert graph["6"]["inputs"]["clip"] == ["10", 1]
    assert graph["7"]["inputs"]["clip"] == ["10", 1]


def test_inject_lora_leaves_conditioning_and_vae_alone():
    """The two inputs a LoRA must NOT touch.

    `positive`/`negative` consume conditioning from CLIPTextEncode; pointing
    them at the loader's CLIP output makes ComfyUI reject the graph outright,
    which is how the LoRA path silently produced nothing. The VAE is not an
    output of LoraLoader at all, so it keeps coming from the checkpoint.
    """
    graph = _txt2img_graph()
    ComfyUIImageProvider._inject_lora(graph, "persona_x.safetensors", 0.8)

    assert graph["3"]["inputs"]["positive"] == ["6", 0]
    assert graph["3"]["inputs"]["negative"] == ["7", 0]
    assert graph["8"]["inputs"]["vae"] == ["4", 2]


def test_inject_lora_handles_the_img2img_graph():
    """The identity-locked path uses img2img, whose graph differs from txt2img:
    it has a VAEEncode reading the checkpoint's VAE."""
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "reference.png"}},
        "2": {"class_type": "VAEEncode", "inputs": {"pixels": ["1", 0], "vae": ["4", 2]}},
        "3": {"class_type": "KSampler", "inputs": {
            "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
            "latent_image": ["2", 0],
        }},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x.safetensors"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": "a"}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": "b"}},
    }
    ComfyUIImageProvider._inject_lora(graph, "persona_x.safetensors", 0.6)

    assert graph["3"]["inputs"]["model"] == ["10", 0]
    assert graph["6"]["inputs"]["clip"] == ["10", 1]
    assert graph["2"]["inputs"]["vae"] == ["4", 2], "VAEEncode must keep the checkpoint's VAE"


def test_inject_lora_is_a_noop_on_a_graph_without_the_checkpoint():
    graph = {"1": {"class_type": "LoadImage", "inputs": {"image": "x.png"}}}
    ComfyUIImageProvider._inject_lora(graph, "persona_x.safetensors", 0.8)
    assert graph["1"]["inputs"] == {"image": "x.png"}
    assert graph["10"]["class_type"] == "LoraLoader"


# ── the trainer must publish where ComfyUI actually reads ────────────

def test_lora_dir_points_at_the_real_comfyui_install(monkeypatch):
    """A path that looks plausible but is not ComfyUI's is the whole bug.

    Resolving the repo root by counting parents is easy to get wrong by one —
    the first attempt landed on `apps/.local/ComfyUI/models/loras`. The trainer
    would have created that directory and copied the adapter into it, reported
    success, and generation would have found no LoRA at all. Anchoring the
    expectation on the ComfyUI tree that actually exists is what makes the
    off-by-one visible instead of silent.
    """
    from app.config import get_settings
    from app.providers.hf_trainer import comfyui_lora_dir

    monkeypatch.setattr(get_settings(), "COMFYUI_LORA_DIR", "", raising=False)
    resolved = comfyui_lora_dir()

    assert resolved.parts[-4:] == (".local", "ComfyUI", "models", "loras"), resolved
    # And the sibling checkpoint ComfyUI loads must sit in the same tree.
    assert (resolved.parent / "checkpoints").is_dir(), (
        f"{resolved} is not inside a ComfyUI install"
    )


def test_lora_dir_honours_an_explicit_override(monkeypatch):
    from app.config import get_settings
    from app.providers.hf_trainer import comfyui_lora_dir

    monkeypatch.setattr(get_settings(), "COMFYUI_LORA_DIR", "/tmp/elsewhere", raising=False)
    assert comfyui_lora_dir() == Path("/tmp/elsewhere")


# ── the saved adapter must be in the format ComfyUI reads ────────────

def test_saved_adapter_uses_the_keys_comfyui_accepts(tmp_path):
    """Verify the save format against a real (tiny) UNet rather than assuming.

    ComfyUI's `comfy/weight_adapter/lora.py` recognises a diffusers adapter by
    the pair `{module}.lora_A.weight` / `{module}.lora_B.weight`, and reads
    lora_alpha from the `lora_adapter_metadata` safetensors header. If the
    trainer's save call produced peft's internal naming (`lora_A.default.weight`)
    or dropped the alpha, ComfyUI would load nothing and generation would look
    like the LoRA had no effect — with no error anywhere.

    Also pins the save API: `save_attn_procs` raises ValueError for a peft
    adapter in diffusers 0.40, so it would fail after training had finished.
    """
    import json

    import torch  # noqa: F401 — imported for the model construction below
    from diffusers import UNet2DConditionModel
    from peft import LoraConfig
    from safetensors import safe_open

    from app.providers.hf_trainer import LORA_TARGET_MODULES

    unet = UNet2DConditionModel(
        sample_size=8, in_channels=4, out_channels=4, layers_per_block=1,
        block_out_channels=(8,), down_block_types=("CrossAttnDownBlock2D",),
        up_block_types=("CrossAttnUpBlock2D",), cross_attention_dim=16,
        attention_head_dim=4, norm_num_groups=4,
    )
    unet.add_adapter(LoraConfig(
        r=4, lora_alpha=8, target_modules=LORA_TARGET_MODULES,
        lora_dropout=0.0, bias="none",
    ))
    unet.save_lora_adapter(str(tmp_path))

    weights = tmp_path / "pytorch_lora_weights.safetensors"
    assert weights.exists(), "the filename ComfyUI is told to look for"

    with safe_open(str(weights), framework="pt") as handle:
        keys = list(handle.keys())
        metadata = handle.metadata() or {}

    assert keys, "no adapter was saved at all"
    assert all(k.endswith((".lora_A.weight", ".lora_B.weight")) for k in keys), keys
    assert all(".default." not in k for k in keys), f"peft-internal naming leaked: {keys}"

    adapter_meta = json.loads(metadata["lora_adapter_metadata"])
    assert adapter_meta["lora_alpha"] == 8


# ── the persona's adapter is looked up honestly ──────────────────────

async def _seed_persona_with_identity(db, lora_path: str, status=IdentityStatus.READY):
    persona = Persona(name=f"Wire_{uuid.uuid4().hex[:6]}", age=24)
    db.add(persona)
    await db.flush()
    identity = Identity(
        id=uuid.uuid4(), persona_id=persona.id, name="cand",
        status=status, lora_model_path=lora_path,
    )
    db.add(identity)
    await db.flush()
    return persona, identity


@pytest.mark.asyncio
async def test_lora_name_is_read_back(db):
    from app.identity_engine import get_persona_lora_name

    persona, _ = await _seed_persona_with_identity(db, "persona_abc.safetensors")
    name = await get_persona_lora_name(persona.id.hex, db)
    assert name == "persona_abc.safetensors"


@pytest.mark.asyncio
async def test_no_adapter_reads_as_empty_not_as_an_error(db):
    """A failed training leaves the persona ACTIVE and usable, so "" is the
    normal answer for it — not an exception."""
    from app.identity_engine import get_persona_lora_name

    persona, _ = await _seed_persona_with_identity(db, "")
    assert await get_persona_lora_name(persona.id.hex, db) == ""


@pytest.mark.asyncio
async def test_ready_identity_is_preferred_over_an_earlier_attempt(db):
    """A persona can accumulate several identities; an older, non-READY one may
    still carry a path from a failed attempt. Applying that would be worse than
    applying nothing."""
    from app.identity_engine import get_persona_lora_name

    persona = Persona(name=f"Multi_{uuid.uuid4().hex[:6]}", age=24)
    db.add(persona)
    await db.flush()
    db.add(Identity(
        id=uuid.uuid4(), persona_id=persona.id, name="first",
        status=IdentityStatus.FAILED, lora_model_path="stale_attempt.safetensors",
    ))
    await db.flush()
    db.add(Identity(
        id=uuid.uuid4(), persona_id=persona.id, name="second",
        status=IdentityStatus.READY, lora_model_path="current.safetensors",
    ))
    await db.flush()

    assert await get_persona_lora_name(persona.id.hex, db) == "current.safetensors"


# ── the identity-locked path passes it to a provider that can use it ──

class _RecordingEditProvider(ImageProvider):
    """Accepts a LoRA, like ComfyUI does."""

    def __init__(self):
        self.calls: list[dict] = []

    @property
    def SUPPORTS_ADULT(self):
        return False

    async def generate(self, prompt, **kwargs):
        return ProviderResult(success=False, error="not used")

    async def img2img(self, image_key, prompt, **kwargs):
        return ProviderResult(success=False, error="not used")

    async def upscale(self, image_key, scale=2):
        return ProviderResult(success=False, error="not used")

    async def edit_image(self, reference_image_bytes, prompt, negative_prompt="",
                         width=1024, height=1024, seed=-1,
                         lora_path="", lora_strength=0.8):
        self.calls.append({"prompt": prompt, "lora_path": lora_path})
        return ProviderResult(
            success=True,
            data={"image_bytes": b"\x89PNG\r\n\x1a\n" + b"x" * 32},
            provider="recording",
        )

    async def health_check(self):
        return ProviderResult(success=True)


class _NoLoraEditProvider(_RecordingEditProvider):
    """Has no adapter concept, like DashScope's edit API."""

    async def edit_image(self, reference_image_bytes, prompt, negative_prompt="",
                         width=1024, height=1024, seed=-1):
        self.calls.append({"prompt": prompt, "lora_path": ""})
        return ProviderResult(
            success=True,
            data={"image_bytes": b"\x89PNG\r\n\x1a\n" + b"x" * 32},
            provider="no_lora",
        )


async def _seed_generation_fixture(db, tmp_path, monkeypatch, lora_path: str):
    """A persona with an identity lock, an avatar on disk, and a LoRA."""
    import app.identity_engine as engine

    persona, _ = await _seed_persona_with_identity(db, lora_path)
    db.add(IdentityLock(
        persona_id=persona.id.hex,
        seed=1234,
        identity_prompt="a woman with green eyes",
        negative_prompt="blurry",
    ))
    await db.flush()

    monkeypatch.setattr(engine, "AVATAR_DIR", Path(tmp_path))
    # A real JPEG: get_avatar_bytes re-encodes the avatar through PIL, so stub
    # bytes would fail before the provider is ever reached.
    from PIL import Image as _Image

    _Image.new("RGB", (8, 8), (120, 90, 60)).save(
        Path(tmp_path) / f"{persona.name.lower()}.jpg", format="JPEG"
    )
    return persona


@pytest.mark.asyncio
async def test_identity_locked_generation_applies_the_persona_lora(
    db, registry_override, tmp_path, monkeypatch
):
    from app.identity_engine import generate_identity_locked

    persona = await _seed_generation_fixture(
        db, tmp_path, monkeypatch, "persona_zara.safetensors"
    )
    provider = registry_override("image", _RecordingEditProvider())

    result = await generate_identity_locked(
        persona_id_hex=persona.id.hex,
        scene_prompt="on a rooftop",
        output_path=str(tmp_path / "out.png"),
        db=db,
    )

    assert result["success"], result.get("error")
    assert provider.calls[0]["lora_path"] == "persona_zara.safetensors"
    assert result["lora_name"] == "persona_zara.safetensors"


@pytest.mark.asyncio
async def test_generation_without_an_adapter_passes_nothing(
    db, registry_override, tmp_path, monkeypatch
):
    from app.identity_engine import generate_identity_locked

    persona = await _seed_generation_fixture(db, tmp_path, monkeypatch, "")
    provider = registry_override("image", _RecordingEditProvider())

    result = await generate_identity_locked(
        persona_id_hex=persona.id.hex,
        scene_prompt="on a rooftop",
        output_path=str(tmp_path / "out.png"),
        db=db,
    )

    assert result["success"], result.get("error")
    assert provider.calls[0]["lora_path"] == ""
    assert result["lora_name"] == ""


@pytest.mark.asyncio
async def test_provider_without_lora_support_is_not_handed_one(
    db, registry_override, tmp_path, monkeypatch
):
    """DashScope's edit API has no adapter concept. Passing the argument anyway
    would raise TypeError on every image, so the parameter is filtered by
    signature — and the result reports that no adapter was applied rather than
    implying the persona's LoRA was used."""
    from app.identity_engine import generate_identity_locked

    persona = await _seed_generation_fixture(
        db, tmp_path, monkeypatch, "persona_zara.safetensors"
    )
    provider = registry_override("image", _NoLoraEditProvider())

    result = await generate_identity_locked(
        persona_id_hex=persona.id.hex,
        scene_prompt="on a rooftop",
        output_path=str(tmp_path / "out.png"),
        db=db,
    )

    assert result["success"], result.get("error")
    assert provider.calls[0]["lora_path"] == ""
    assert result["lora_name"] == ""


# ── the gate: an adapter may only render on the base it was trained on ──
#
# `lora_base_state` was already computed and surfaced on the persona summary
# while every render path walked straight past it, so a swapped base was
# reported after the sale instead of refused before it. These pin the refusal
# happening *before* the provider runs.


@pytest.mark.asyncio
async def test_the_adult_path_refuses_an_adapter_trained_on_another_base(
    db, registry_override, tmp_path, monkeypatch
):
    """Naomi's measured failure, now stopped at the door.

    The adapter was trained on stock `sd_xl_base_1.0` and every realism plate
    was rendered on `RealVisXL_V4.0`. Both are SDXL, so the family guard agreed
    and nothing objected — while the rendered face was identical to no adapter
    at all. Same family is not same identity.
    """
    from sqlalchemy import select

    from app.config import get_settings
    from app.identity_engine import generate_identity_locked

    monkeypatch.setattr(
        get_settings(), "COMFYUI_CHECKPOINT", "Lustify.safetensors", raising=False
    )
    persona = await _seed_generation_fixture(
        db, tmp_path, monkeypatch, "persona_zara.safetensors"
    )
    identity = (
        await db.execute(select(Identity).where(Identity.persona_id == persona.id))
    ).scalars().first()
    identity.metadata_json = {
        "lora_training_base": {
            "checkpoint": "sd_xl_base_1.0.safetensors",
            "family": "sdxl",
        }
    }
    await db.commit()

    provider = registry_override("image", _RecordingEditProvider())

    result = await generate_identity_locked(
        persona_id_hex=persona.id.hex,
        scene_prompt="on a rooftop",
        output_path=str(tmp_path / "out.png"),
        db=db,
        adult=True,
    )

    assert not result["success"]
    assert result["identity_gate_state"] == "mismatch"
    assert "sd_xl_base_1.0.safetensors" in result["error"]
    # The half that matters: nothing was sampled, so no stranger was rendered
    # and no file was written under her name.
    assert provider.calls == []


@pytest.mark.asyncio
async def test_the_adult_path_refuses_an_adapter_with_no_recorded_base(
    db, registry_override, tmp_path, monkeypatch
):
    """`unknown` is not a pass. An adapter from before the record was kept
    cannot be shown to match, and the adult path is where that fails closed."""
    from app.config import get_settings
    from app.identity_engine import generate_identity_locked

    monkeypatch.setattr(
        get_settings(), "COMFYUI_CHECKPOINT", "Lustify.safetensors", raising=False
    )
    persona = await _seed_generation_fixture(
        db, tmp_path, monkeypatch, "persona_zara.safetensors"
    )
    provider = registry_override("image", _RecordingEditProvider())

    result = await generate_identity_locked(
        persona_id_hex=persona.id.hex,
        scene_prompt="on a rooftop",
        output_path=str(tmp_path / "out.png"),
        db=db,
        adult=True,
    )

    assert not result["success"]
    assert result["identity_gate_state"] == "unknown"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_the_same_persona_still_renders_on_the_plate_path(
    db, registry_override, tmp_path, monkeypatch
):
    """Fail-closed on adult, merely honest on plate. A persona whose adapter
    predates the record is still a working persona, and a lifestyle shot is not
    selling the face in the same way — blocking it would take the product down
    to enforce a rule about a path this is not."""
    from app.config import get_settings
    from app.identity_engine import generate_identity_locked

    monkeypatch.setattr(
        get_settings(), "COMFYUI_CHECKPOINT", "Lustify.safetensors", raising=False
    )
    persona = await _seed_generation_fixture(
        db, tmp_path, monkeypatch, "persona_zara.safetensors"
    )
    provider = registry_override("image", _RecordingEditProvider())

    result = await generate_identity_locked(
        persona_id_hex=persona.id.hex,
        scene_prompt="on a rooftop",
        output_path=str(tmp_path / "out.png"),
        db=db,
    )

    assert result["success"], result.get("error")
    assert provider.calls[0]["lora_path"] == "persona_zara.safetensors"


# ── the trainer must prefer the checkpoint that is already on disk ───

def test_a_local_checkpoint_is_preferred_over_the_hf_repo():
    """The repo's SDXL UNet is fp32-only (`unet/diffusion_pytorch_model
    .safetensors`, no `.fp16` variant), so downloading it means ~12 GB of
    transfer that a resumable-retry loop cannot reliably finish on a flaky
    connection — and it re-downloads what ComfyUI already has on disk.

    Loading the local single-file checkpoint removes the transfer entirely and
    is strictly better than the download even when the download works: the
    adapter is then trained against the exact weights the image path generates
    with, so the base-family mismatch cannot occur at all.
    """
    from app.providers.hf_trainer import comfyui_checkpoint_dir, local_checkpoint_path

    resolved = local_checkpoint_path()
    assert resolved is not None, (
        f"no checkpoint at {comfyui_checkpoint_dir()} — the trainer would fall "
        "back to a full SDXL download"
    )
    assert resolved.is_file()
    assert resolved.parent == comfyui_checkpoint_dir()
    assert resolved.name.endswith((".safetensors", ".ckpt"))


def test_the_checkpoint_dir_is_the_sibling_of_the_lora_dir():
    """Both are derived the same way, so an override of one must move the other
    — otherwise a relocated ComfyUI would train against one install's
    checkpoint and publish the adapter into another's LoRA directory."""
    from app.providers.hf_trainer import comfyui_checkpoint_dir, comfyui_lora_dir

    assert comfyui_checkpoint_dir() == comfyui_lora_dir().parent / "checkpoints"


def test_a_missing_checkpoint_falls_back_rather_than_failing(monkeypatch, tmp_path):
    """An operator on a fresh machine with no checkpoint yet must still be able
    to train: `local_checkpoint_path` returns None and the caller downloads
    instead."""
    from app.config import get_settings
    from app.providers.hf_trainer import local_checkpoint_path

    monkeypatch.setattr(get_settings(), "COMFYUI_LORA_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(get_settings(), "COMFYUI_CHECKPOINT", "not_here.safetensors",
                        raising=False)
    assert local_checkpoint_path() is None


# ── training precision must not silently produce an untrained adapter ──

def test_the_dtype_rule_prefers_float32_and_halves_only_when_it_must(monkeypatch):
    """The rule, stated against machines rather than against this one.

    float32 is the preference because the fp16 forward overflows to a non-finite
    loss — that is what made SDXL training skip 8 of 9 steps. float16 is the
    concession to memory, taken only when the weights plus working room do not
    fit. Pinning the rule to the machine running the tests would make it pass
    here and mean nothing anywhere else.
    """
    from app.providers import hf_trainer
    from app.providers.hf_trainer import UNET_PARAMS

    fits = hf_trainer._fits_in_accelerator
    sdxl_fp32_gb = UNET_PARAMS["sdxl"] * 4 / 1e9
    assert 10.0 < sdxl_fp32_gb < 10.5, "SDXL's UNet is ~10.3 GB in float32"

    # A 24 GB card takes the whole UNet at fp32 with room to spare.
    assert fits(UNET_PARAMS["sdxl"], 4, limit_gb=24.0) is True
    # 12 GB does not: 10.3 GB of weights leaves under 2 GB for everything else.
    assert fits(UNET_PARAMS["sdxl"], 4, limit_gb=12.0) is False
    # SD 1.5 is ten times smaller and fits almost anywhere.
    assert fits(UNET_PARAMS["sd15"], 4, limit_gb=8.0) is True

    # No accelerator at all: not a budget question. The detected budget is what
    # makes this machine-dependent, so the detection is what gets replaced.
    monkeypatch.setattr(hf_trainer, "_accelerator_limit_gb", lambda: None)
    assert fits(UNET_PARAMS["sd15"], 4) is False


def test_auto_dtype_chooses_by_size_and_falls_back_when_unsized(monkeypatch):
    """What the trainer actually gets, and the safe answer when the size is not
    known — the fallback, because wrongly assuming float32 fits fails mid-run."""
    import torch

    from app.providers import hf_trainer
    from app.providers.hf_trainer import UNET_PARAMS, training_dtype

    monkeypatch.setattr(hf_trainer, "_bfloat16_supported", lambda device: True)

    # SD 1.5's UNet is small enough to train at float32 anywhere.
    assert training_dtype("sd15", "mps", param_count=UNET_PARAMS["sd15"]) == torch.float32
    # A family with no entry in UNET_PARAMS has no known size. Conservative: the
    # fallback, not the preference.
    assert training_dtype("some-future-architecture", "mps") == torch.bfloat16
    # CPU has no fixed budget, so it takes float32 rather than asking the device.
    assert training_dtype("some-future-architecture", "cpu") == torch.float32
    # No plausible budget holds a 60 GB UNet, so it halves wherever it runs.
    assert training_dtype("sdxl", "mps", param_count=60_000_000_000) == torch.bfloat16


def test_the_fallback_is_bfloat16_because_float16_is_the_one_that_skips_steps(monkeypatch):
    """Both are 2 bytes; only one keeps float32's exponent range.

    float16 was the old fallback, and on this machine it skipped 8 of 9 steps —
    the adapter it saved was close to its random initialization, which is the
    outcome the loop's guard now refuses. bfloat16 landed every step on the same
    machine, same data, same 1024px, at roughly 8x float32's speed.
    """
    import torch

    from app.providers import hf_trainer
    from app.providers.hf_trainer import training_dtype

    monkeypatch.setattr(hf_trainer, "_bfloat16_supported", lambda device: True)

    assert training_dtype("sdxl", "mps", param_count=60_000_000_000) == torch.bfloat16
    assert training_dtype("sd15", "mps", param_count=60_000_000_000) == torch.bfloat16


def test_an_accelerator_without_bfloat16_gets_float16_not_a_silent_emulation(monkeypatch):
    """bfloat16 needs hardware float16 does not — Metal from macOS 14, CUDA 8.0+.
    Where it is missing the request does not fail loudly; it emulates or
    promotes, so the capability has to be asked for rather than assumed."""
    import torch

    from app.providers import hf_trainer
    from app.providers.hf_trainer import _bfloat16_supported, training_dtype

    monkeypatch.setattr(hf_trainer, "_bfloat16_supported", lambda device: False)

    assert training_dtype("sdxl", "mps", param_count=60_000_000_000) == torch.float16
    # CPU never reaches the question: it is answered float32 before the fallback.
    assert training_dtype("sdxl", "cpu") == torch.float32
    # Plain CPU is not an accelerator that can run the bf16 pass.
    assert _bfloat16_supported("cpu") is False


def test_the_family_supplies_the_default_size_so_auto_is_not_guessing():
    """`param_count` is optional because the family's own UNet size is the right
    default. Without this, `training_dtype("sd15", …)` on any machine would fall
    to float16 — halving a UNet that fits in float32 with room to spare, and
    carrying the fp16 overflow risk for no reason."""
    import torch

    from app.providers.hf_trainer import UNET_PARAMS, training_dtype

    # 860M at fp32 is 3.4 GB: it fits even in a modest budget, so the answer is
    # the same whether the size is passed or derived.
    assert training_dtype("sd15", "mps") == training_dtype(
        "sd15", "mps", param_count=UNET_PARAMS["sd15"]
    ) == torch.float32


def test_the_trainer_asks_for_the_size_of_the_family_it_is_training():
    """The wiring, not the rule: SDXL must be sized as SDXL.

    Passing the wrong family's count would silently give SDXL a budget meant for
    a ten-times-smaller UNet, which is how a run gets half its weights and an
    allocation failure.
    """
    import inspect

    from app.providers import hf_trainer

    source = inspect.getsource(hf_trainer.HuggingFaceTrainer._train_sync)
    assert "param_count=UNET_PARAMS.get(family)" in source, (
        "the trainer must size the dtype decision by family, or the auto path "
        "is back to guessing"
    )


def test_a_pinned_dtype_wins_and_a_typo_is_refused():
    import torch

    from app.providers.hf_trainer import training_dtype

    assert training_dtype("sdxl", "mps", "float32") == torch.float32
    assert training_dtype("sd15", "cpu", "fp16") == torch.float16
    assert training_dtype("sdxl", "cpu", "bf16") == torch.bfloat16
    with pytest.raises(ValueError, match="TRAINER_DTYPE"):
        training_dtype("sdxl", "mps", "bfloat17")


def test_a_dtype_that_needs_no_loss_scaling_is_not_scaled():
    """The defect this pins, measured on this machine.

    `scale` was 1024.0 for every dtype. The backward multiplied by it
    unconditionally, while the unscale that divides it back out is guarded by
    `use_scaling`. So every float32 and bfloat16 run stepped its optimizer 1024x
    too far — trained at 1024x the configured learning rate — and still reported
    a small, healthy-looking loss, because the loss is computed before the
    multiply. Both the fp32 and the bf16 probe runs were trained that way; their
    timings are real, their loss values are not evidence of a good fit.

    Only float16 needs the scale at all, and it is the only dtype that gets it.
    """
    import torch

    from app.providers.hf_trainer import _initial_loss_scale

    assert _initial_loss_scale(torch.float16) == 1024.0
    assert _initial_loss_scale(torch.bfloat16) == 1.0
    assert _initial_loss_scale(torch.float32) == 1.0


def test_the_training_loop_scales_by_a_factor_it_will_actually_undo():
    """The wiring: the loop must take its scale from that rule rather than
    restating it, and a hardcoded 1024.0 here is the 1024x learning rate back."""
    import inspect

    from app.providers import hf_trainer

    source = inspect.getsource(hf_trainer.HuggingFaceTrainer._train_sync)
    assert "scale = _initial_loss_scale(dtype)" in source
    assert "scale = 1024.0" not in source, (
        "a scale the unscale guard skips is a 1024x learning rate on every "
        "dtype that is not float16"
    )


def test_the_trainer_reports_the_weights_it_will_load():
    """`weights_source` is the operator's only signal that training will read
    the local checkpoint rather than pull ~12 GB from the Hub."""
    import asyncio

    from app.providers.hf_trainer import HuggingFaceTrainer, local_checkpoint_path

    result = asyncio.run(HuggingFaceTrainer().health_check())
    assert result.success, result.error
    expected = local_checkpoint_path()
    if expected is not None:
        assert result.data["uses_local_checkpoint"] is True
        assert result.data["weights_source"] == str(expected)
    assert result.data["family"] == "sdxl"
    assert result.data["comfyui_lora_dir"].endswith("ComfyUI/models/loras")
