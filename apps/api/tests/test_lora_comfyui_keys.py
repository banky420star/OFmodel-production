"""Persona Studio — the LoRA must be renamed before ComfyUI can load it.

The trainer published its adapter with `shutil.copy2` and logged
`lora_published_to_comfyui`. Everything downstream looked right: the file landed
in ComfyUI's `models/loras/`, the build recorded `comfyui_lora_name`, and a live
generation submitted a `LoraLoader` node naming it. It still applied **nothing**.

Measured on the real trained adapter (Naomi, 1120 tensors): ComfyUI wrote
`lora key not loaded` for every tensor — 2240 lines, once per patch pass — and
the same tensors with `.processor.` inserted produced **0**. The base name never
matched. `comfy/weight_adapter/lora.py:LoRAAdapter.load` reads
`{base}.lora_A.weight`, and `comfy/lora.py:model_lora_keys_unet` builds its
diffusers entries as `k.replace(".to_", ".processor.to_")` over the UNet's module
names — a substitution `save_lora_adapter` never made.

`peft`'s suffix (`*.lora_A.weight`) was never the problem, which is what the
earlier comment in hf_trainer assumed. The `.to_out.0` -> `.to_out` part is the
second half of the same map.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.providers.hf_trainer import comfyui_lora_key, publish_comfyui_lora


# ── the rename ───────────────────────────────────────────────────────

def test_to_q_kv_gain_a_processor_segment():
    base = "down_blocks.1.attentions.0.transformer_blocks.0.attn1"
    assert comfyui_lora_key(f"{base}.to_q.lora_A.weight") == (
        f"{base}.processor.to_q.lora_A.weight"
    )
    assert comfyui_lora_key(f"{base}.to_k.lora_B.weight") == (
        f"{base}.processor.to_k.lora_B.weight"
    )
    assert comfyui_lora_key(f"{base}.to_v.lora_A.weight") == (
        f"{base}.processor.to_v.lora_A.weight"
    )


def test_to_out_drops_the_trailing_dot_zero():
    """The map strips `.0` from `to_out`, so keeping it would miss by one segment."""
    base = "down_blocks.0.attentions.1.transformer_blocks.0.attn2"
    assert comfyui_lora_key(f"{base}.to_out.0.lora_A.weight") == (
        f"{base}.processor.to_out.lora_A.weight"
    )


def test_alpha_rows_are_renamed_too():
    base = "up_blocks.2.attentions.1.transformer_blocks.0.attn1"
    assert comfyui_lora_key(f"{base}.to_q.alpha") == f"{base}.processor.to_q.alpha"


def test_a_key_without_a_known_suffix_is_still_renamed():
    key = "mid_block.attentions.0.transformer_blocks.0.attn1.to_q.some_new_suffix"
    assert comfyui_lora_key(key) == (
        "mid_block.attentions.0.transformer_blocks.0.attn1.processor.to_q"
        ".some_new_suffix"
    )


def test_the_transform_is_not_idempotent():
    """Documented, not desired: applying it twice yields `.processor.processor.`.

    Pinned so nobody "simplifies" the trainer by re-running it over an
    already-published file — that would silently reproduce the zero-match
    failure it exists to prevent.
    """
    once = comfyui_lora_key("attn1.to_q.lora_A.weight")
    twice = comfyui_lora_key(once)
    assert once == "attn1.processor.to_q.lora_A.weight"
    assert twice == "attn1.processor.processor.to_q.lora_A.weight"
    assert twice != once


# ── why the rename is necessary, stated as a check rather than a claim ─

def _comfy_key_table(module_names: list[str]) -> set[str]:
    """Stand in for `model_lora_keys_unet`: the names ComfyUI will look up.

    Same two rules as the real map — insert `.processor.`, drop `to_out`'s `.0`.
    """
    table = set()
    for name in module_names:
        entry = name.replace(".to_", ".processor.to_")
        if entry.endswith(".processor.to_out.0"):
            entry = entry[: -len(".0")]
        table.add(entry)
    return table


def test_the_trainers_own_keys_would_all_miss():
    """Falsifies the old byte-copy: zero of the raw keys are in ComfyUI's table."""
    module_names = [
        "down_blocks.1.attentions.0.transformer_blocks.0.attn1.to_q",
        "down_blocks.1.attentions.0.transformer_blocks.0.attn1.to_out.0",
        "up_blocks.0.attentions.2.transformer_blocks.1.attn2.to_k",
    ]
    table = _comfy_key_table(module_names)

    raw = [f"{name}.lora_A.weight" for name in module_names]
    renamed = [comfyui_lora_key(key) for key in raw]

    raw_bases = [key[: -len(".lora_A.weight")] for key in raw]
    renamed_bases = [key[: -len(".lora_A.weight")] for key in renamed]

    assert not any(base in table for base in raw_bases), (
        "the raw trainer keys must not match — this is the bug the rename fixes"
    )
    assert all(base in table for base in renamed_bases)


# ── the publish, on real safetensors ─────────────────────────────────

def _write_adapter(path: Path, metadata: dict[str, str] | None = None) -> dict:
    import torch
    from safetensors.torch import save_file

    tensors = {
        "down_blocks.1.attentions.0.transformer_blocks.0.attn1.to_q.lora_A.weight":
            torch.zeros(4, 8),
        "down_blocks.1.attentions.0.transformer_blocks.0.attn1.to_q.lora_B.weight":
            torch.ones(8, 4),
        "down_blocks.1.attentions.0.transformer_blocks.0.attn1.to_out.0.lora_A.weight":
            torch.full((4, 8), 0.5),
        "down_blocks.1.attentions.0.transformer_blocks.0.attn1.to_q.alpha":
            torch.tensor(8.0),
    }
    save_file(tensors, str(path), metadata=metadata or {})
    return tensors


def test_publish_renames_every_tensor(tmp_path):
    from safetensors import safe_open

    source = tmp_path / "pytorch_lora_weights.safetensors"
    _write_adapter(source)
    destination = tmp_path / "loras" / "persona_ds.safetensors"
    destination.parent.mkdir(parents=True)

    count = publish_comfyui_lora(source, destination)
    assert count == 4

    with safe_open(str(destination), framework="pt") as handle:
        keys = set(handle.keys())
    assert keys == {
        "down_blocks.1.attentions.0.transformer_blocks.0.attn1.processor.to_q.lora_A.weight",
        "down_blocks.1.attentions.0.transformer_blocks.0.attn1.processor.to_q.lora_B.weight",
        "down_blocks.1.attentions.0.transformer_blocks.0.attn1.processor.to_out.lora_A.weight",
        "down_blocks.1.attentions.0.transformer_blocks.0.attn1.processor.to_q.alpha",
    }


def test_publish_keeps_values_and_metadata(tmp_path):
    """A rename, not a re-train: the peft LoraConfig (lora_alpha) must survive."""
    import torch
    from safetensors import safe_open

    source = tmp_path / "src.safetensors"
    _write_adapter(source, metadata={"lora_adapter_metadata": '{"lora_alpha": 16}'})
    destination = tmp_path / "dst.safetensors"

    publish_comfyui_lora(source, destination)

    with safe_open(str(destination), framework="pt") as handle:
        assert handle.metadata().get("lora_adapter_metadata") == '{"lora_alpha": 16}'
        values = {
            key: handle.get_tensor(key)
            for key in handle.keys()
        }

    assert torch.equal(
        values["down_blocks.1.attentions.0.transformer_blocks.0.attn1.processor.to_q.lora_B.weight"],
        torch.ones(8, 4),
    )
    assert torch.allclose(
        values["down_blocks.1.attentions.0.transformer_blocks.0.attn1.processor.to_out.lora_A.weight"],
        torch.full((4, 8), 0.5),
    )
    assert float(
        values["down_blocks.1.attentions.0.transformer_blocks.0.attn1.processor.to_q.alpha"]
    ) == 8.0


def test_publish_leaves_the_diffusers_original_alone(tmp_path):
    """Any diffusers-based path still needs the un-renamed keys."""
    from safetensors import safe_open

    source = tmp_path / "pytorch_lora_weights.safetensors"
    _write_adapter(source)
    publish_comfyui_lora(source, tmp_path / "comfy.safetensors")

    with safe_open(str(source), framework="pt") as handle:
        assert any(key.endswith(".to_q.lora_A.weight") for key in handle.keys())
        assert not any("processor" in key for key in handle.keys())


# ── wiring guard ─────────────────────────────────────────────────────

def test_the_trainer_publishes_through_the_rename():
    """The publish step must call `publish_comfyui_lora`, not byte-copy.

    A source-level guard, deliberately: `train()` loads a real SDXL pipeline, so
    a behavioural test of that branch is not reachable in the suite. It pins the
    one line that was wrong, and it fails loudly if the copy ever comes back.
    """
    import inspect

    import app.providers.hf_trainer as trainer_module

    source = inspect.getsource(trainer_module)
    assert "publish_comfyui_lora(" in source
    assert "shutil.copy2" not in source, (
        "a byte copy keeps the trainer's key names, which ComfyUI maps zero of"
    )
