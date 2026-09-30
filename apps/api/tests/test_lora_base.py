"""Was the identity adapter trained on the checkpoint that will render it?

The failure this exists for is measured, not hypothetical: Naomi's adapter was
trained on stock `sd_xl_base_1.0` and every realism plate was rendered on
`RealVisXL_V4.0`. Both are SDXL, so the trainer's family guard passed and nothing
anywhere reported a problem — while the rendered face was identical to no adapter
at all. The tests below pin the three states and, most importantly, that the
honest answer for an unrecorded base is `unknown` rather than a pass.
"""

from __future__ import annotations

import uuid

import pytest

from app.lora_base import (
    MATCH,
    MISMATCH,
    UNKNOWN,
    GateError,
    PathPolicy,
    base_mismatch,
    enforce_before_render,
    training_base_from_steps,
)
from app.models import Identity, IdentityStatus, Persona, persona_identity_summary


# ── reading the base off the training step ──────────────────────────────


def test_it_reads_the_checkpoint_the_trainer_reported():
    steps = {
        "step_5": {
            "training_checkpoint": "sd_xl_base_1.0.safetensors",
            "base_model": "stabilityai/stable-diffusion-xl-base-1.0",
            "lora_family": "sdxl",
        }
    }
    base = training_base_from_steps(steps)
    assert base["checkpoint"] == "sd_xl_base_1.0.safetensors"
    assert base["family"] == "sdxl"


def test_an_absolute_source_path_yields_the_filename():
    steps = {
        "step_3": {
            "training_source": "/Volumes/AI_DRIVE/ComfyUI/models/checkpoints/RealVisXL_V4.0.safetensors",
            "base_model": "stabilityai/stable-diffusion-xl-base-1.0",
        }
    }
    assert training_base_from_steps(steps)["checkpoint"] == "RealVisXL_V4.0.safetensors"


def test_an_hf_repo_source_is_not_mistaken_for_a_file():
    """`hf:<repo>` names no file. Treating it as one would record a checkpoint
    that does not exist and could match a render checkpoint by accident."""
    steps = {
        "step_2": {
            "training_source": "hf:stabilityai/stable-diffusion-xl-base-1.0",
            "base_model": "stabilityai/stable-diffusion-xl-base-1.0",
        }
    }
    base = training_base_from_steps(steps)
    assert base["checkpoint"] == ""
    assert base["base_model"] == "stabilityai/stable-diffusion-xl-base-1.0"


def test_step_order_is_numeric_not_lexicographic():
    """The mirror of the `step_10`-before-`step_2` bug `_step_order` exists for."""
    steps = {
        "step_10": {"training_checkpoint": "late.safetensors"},
        "step_2": {"training_checkpoint": "early.safetensors"},
    }
    assert training_base_from_steps(steps)["checkpoint"] == "early.safetensors"


def test_a_step_without_a_base_is_skipped():
    steps = {"step_1": {"total_images": 9}, "step_4": {"training_checkpoint": "a.safetensors"}}
    assert training_base_from_steps(steps)["checkpoint"] == "a.safetensors"


def test_no_base_anywhere_is_an_empty_record():
    assert training_base_from_steps({"step_1": {"total_images": 9}}) == {}
    assert training_base_from_steps({}) == {}


# ── the verdict: three states, and unknown is not a pass ────────────────


def test_the_realvis_mismatch_is_reported():
    """The exact case that shipped silently."""
    recorded = {"checkpoint": "sd_xl_base_1.0.safetensors", "family": "sdxl"}
    verdict = base_mismatch(recorded, "RealVisXL_V4.0.safetensors")
    assert verdict["state"] == MISMATCH
    assert "sd_xl_base_1.0.safetensors" in verdict["reason"]
    assert "RealVisXL_V4.0.safetensors" in verdict["reason"]


def test_a_same_file_match_is_a_match():
    assert base_mismatch(
        {"checkpoint": "RealVisXL_V4.0.safetensors"}, "RealVisXL_V4.0.safetensors"
    )["state"] == MATCH


def test_a_match_is_case_insensitive():
    """Filenames are not typed consistently, and a case difference is not a
    different checkpoint — reporting it as a mismatch would train an operator to
    ignore the warning."""
    assert base_mismatch(
        {"checkpoint": "realvisxl_v4.0.safetensors"}, "RealVisXL_V4.0.safetensors"
    )["state"] == MATCH


def test_an_unrecorded_base_is_unknown_not_a_pass():
    verdict = base_mismatch(None, "RealVisXL_V4.0.safetensors")
    assert verdict["state"] == UNKNOWN
    assert "never recorded" in verdict["reason"]


def test_family_only_agreement_is_still_unknown():
    """The trap itself. Every SDXL finetune shares a family, so a recorded family
    agreeing must never be read as a match — that is precisely how the
    SDXL-base-vs-RealVisXL swap went unnoticed."""
    recorded = {"base_model": "stabilityai/stable-diffusion-xl-base-1.0", "family": "sdxl"}
    verdict = base_mismatch(recorded, "RealVisXL_V4.0.safetensors")
    assert verdict["state"] == UNKNOWN
    assert "cannot be ruled out" in verdict["reason"]


def test_a_family_mismatch_is_a_mismatch():
    recorded = {"base_model": "runwayml/stable-diffusion-v1-5", "family": "sd15"}
    assert base_mismatch(recorded, "RealVisXL_V4.0.safetensors")["state"] == MISMATCH


def test_no_render_checkpoint_is_unknown():
    assert base_mismatch({"checkpoint": "x.safetensors"}, "")["state"] == UNKNOWN


def test_a_windows_path_is_read_as_a_filename():
    recorded = {"checkpoint": r"C:\models\RealVisXL_V4.0.safetensors"}
    assert base_mismatch(recorded, "RealVisXL_V4.0.safetensors")["state"] == MATCH


# ── surfaced on the identity summary the API returns ────────────────────


async def _persona(db, name=None):
    # `personas.name` is UNIQUE and a committed row outlives the `db` fixture's
    # rollback, so two tests reusing a name collide instead of starting clean.
    persona = Persona(id=uuid.uuid4(), name=name or f"Lora-{uuid.uuid4().hex[:8]}", age=25)
    db.add(persona)
    await db.commit()
    return persona


async def test_the_summary_reports_the_mismatch(db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(
        get_settings(), "COMFYUI_CHECKPOINT", "RealVisXL_V4.0.safetensors", raising=False
    )
    persona = await _persona(db)
    db.add(
        Identity(
            id=uuid.uuid4(),
            persona_id=persona.id,
            name="Naomi Solara",
            status=IdentityStatus.READY,
            lora_model_path="persona_x.safetensors",
            metadata_json={
                "lora_training_base": {"checkpoint": "sd_xl_base_1.0.safetensors", "family": "sdxl"}
            },
        )
    )
    await db.commit()

    summary = await persona_identity_summary(db, [persona.id])
    assert summary[str(persona.id)]["lora_base_state"] == MISMATCH
    assert "RealVisXL_V4.0.safetensors" in summary[str(persona.id)]["lora_base_note"]


async def test_the_summary_reports_unknown_for_an_unrecorded_base(db, monkeypatch):
    """Naomi's real state: an adapter exists, trained before the record was kept."""
    from app.config import get_settings

    monkeypatch.setattr(
        get_settings(), "COMFYUI_CHECKPOINT", "RealVisXL_V4.0.safetensors", raising=False
    )
    persona = await _persona(db)
    db.add(
        Identity(
            id=uuid.uuid4(),
            persona_id=persona.id,
            name="Legacy",
            status=IdentityStatus.READY,
            lora_model_path="persona_legacy.safetensors",
            metadata_json={},
        )
    )
    await db.commit()

    summary = await persona_identity_summary(db, [persona.id])
    assert summary[str(persona.id)]["lora_base_state"] == UNKNOWN


async def test_the_summary_invents_no_verdict_without_an_adapter(db):
    """No LoRA means no base to check. Reporting `unknown` there would be a
    problem invented rather than found."""
    persona = await _persona(db)
    db.add(
        Identity(
            id=uuid.uuid4(),
            persona_id=persona.id,
            name="Untrained",
            status=IdentityStatus.CANDIDATE,
            lora_model_path="",
            metadata_json={},
        )
    )
    await db.commit()

    summary = await persona_identity_summary(db, [persona.id])
    assert summary[str(persona.id)]["lora_base_state"] == ""
    assert summary[str(persona.id)]["lora_base_note"] == ""


# ── enforcement: the render path has to refuse, not merely report ────────
#
# Everything above pins the verdict. These pin what the verdict *does* — the
# gap being that `lora_base_state` was computed and surfaced while every render
# path walked past it.


def test_a_match_renders_on_both_paths():
    recorded = {"checkpoint": "Lustify.safetensors"}
    for path in (PathPolicy.ADULT, PathPolicy.PLATE):
        decision = enforce_before_render(recorded, "Lustify.safetensors", path=path)
        assert decision.allowed
        assert decision.state == MATCH
        assert decision.warn is False


def test_the_measured_swap_refuses_on_the_adult_path():
    """Naomi's adapter, trained on stock SDXL and rendered on a photoreal
    finetune. It used to be reported after the sale; now it stops the render."""
    recorded = {"checkpoint": "sd_xl_base_1.0.safetensors", "family": "sdxl"}
    with pytest.raises(GateError) as caught:
        enforce_before_render(recorded, "RealVisXL_V4.0.safetensors", path=PathPolicy.ADULT)

    assert caught.value.decision.state == MISMATCH
    assert "RealVisXL_V4.0.safetensors" in caught.value.decision.reason


def test_a_known_mismatch_refuses_on_the_plate_path_too():
    """Plate is more permissive only about the *unverifiable* case. A mismatch
    that is known is a mismatch wherever it is found."""
    with pytest.raises(GateError):
        enforce_before_render(
            {"checkpoint": "sd_xl_base_1.0.safetensors"},
            "Lustify.safetensors",
            path=PathPolicy.PLATE,
        )


def test_an_unrecorded_base_fails_closed_on_adult():
    with pytest.raises(GateError) as caught:
        enforce_before_render(None, "Lustify.safetensors", path=PathPolicy.ADULT)
    assert caught.value.decision.state == UNKNOWN


def test_an_unrecorded_base_warns_and_continues_on_plate():
    """A persona whose adapter predates the record is still a working persona.
    Blocking plate content for it would take the product down to enforce a rule
    about a face that path is not selling."""
    decision = enforce_before_render(None, "Lustify.safetensors", path=PathPolicy.PLATE)
    assert decision.allowed
    assert decision.warn is True
    assert decision.state == UNKNOWN


def test_the_refusal_is_an_exception_not_a_none_to_shrug_off():
    """There is no bare-checkpoint fallback to return instead."""
    with pytest.raises(GateError):
        enforce_before_render({"checkpoint": "a.safetensors"}, "b.safetensors")


# ── two ways a caller hands over something that is not a base ────────────


def test_a_capability_flag_is_not_a_checkpoint_name():
    """`COMFYUI_ADULT_CHECKPOINT` is a bool. Stringified it becomes the filename
    `True`, which matches no adapter — so arming the flag refused every render on
    exactly the path it exists to protect, and did it silently."""
    assert base_mismatch({"checkpoint": "Lustify.safetensors"}, True)["state"] == UNKNOWN
    assert base_mismatch({"checkpoint": "Lustify.safetensors"}, False)["state"] == UNKNOWN
    with pytest.raises(GateError):
        enforce_before_render({"checkpoint": "Lustify.safetensors"}, True)


def test_a_verdict_passed_as_a_base_is_never_a_match():
    """`lora_base_state` holds `match`/`mismatch`/`unknown`. Handing that verdict
    back where a checkpoint name belongs must not read as agreement: the thing a
    render needs is the recorded base, not the answer about it."""
    for verdict in (MATCH, MISMATCH, UNKNOWN):
        assert base_mismatch({"checkpoint": verdict}, "Lustify.safetensors")["state"] != MATCH


def test_a_recorded_base_that_is_not_a_dict_is_unrecorded_not_a_crash():
    """The field is free-form JSON on the identity row, so it is whatever was
    written there. A bare string is a row that cannot be checked — `unknown`,
    the same as no record at all. Raising on it would take the persona summary
    down for every persona, because that summary reads every identity."""
    assert base_mismatch("Lustify.safetensors", "Lustify.safetensors")["state"] == UNKNOWN
    assert base_mismatch([], "Lustify.safetensors")["state"] == UNKNOWN
    assert base_mismatch(42, "Lustify.safetensors")["state"] == UNKNOWN


# ── the adapter and its base must come off one identity row ─────────────


async def test_the_adapter_and_its_recorded_base_come_from_the_same_row(db):
    """Reading the LoRA from the READY identity and the base in a second query
    would let the two land on different candidates, and the gate would then
    report a mismatch that the lookup invented rather than the weights."""
    from app.identity_engine import get_persona_adapter

    persona = await _persona(db)
    db.add_all([
        Identity(
            id=uuid.uuid4(),
            persona_id=persona.id,
            name="Superseded",
            status=IdentityStatus.CANDIDATE,
            lora_model_path="superseded.safetensors",
            metadata_json={"lora_training_base": {"checkpoint": "RealVisXL_V4.0.safetensors"}},
        ),
        Identity(
            id=uuid.uuid4(),
            persona_id=persona.id,
            name="Current",
            status=IdentityStatus.READY,
            lora_model_path="current.safetensors",
            metadata_json={"lora_training_base": {"checkpoint": "Lustify.safetensors"}},
        ),
    ])
    await db.commit()

    adapter = await get_persona_adapter(persona.id.hex, db)
    assert adapter["lora_name"] == "current.safetensors"
    assert adapter["recorded_base"]["checkpoint"] == "Lustify.safetensors"


async def test_a_persona_with_no_adapter_has_no_recorded_base(db):
    from app.identity_engine import get_persona_adapter

    persona = await _persona(db)
    adapter = await get_persona_adapter(persona.id.hex, db)
    assert adapter == {"lora_name": "", "recorded_base": None}


async def test_a_non_dict_recorded_base_is_not_treated_as_one(db):
    """The field is free-form JSON on the identity. Anything that is not a dict
    is 'not recorded', which the gate reports as unknown — never as a match."""
    from app.identity_engine import get_persona_adapter

    persona = await _persona(db)
    db.add(
        Identity(
            id=uuid.uuid4(),
            persona_id=persona.id,
            name="Stringly",
            status=IdentityStatus.READY,
            lora_model_path="persona_x.safetensors",
            metadata_json={"lora_training_base": "Lustify.safetensors"},
        )
    )
    await db.commit()

    adapter = await get_persona_adapter(persona.id.hex, db)
    assert adapter["lora_name"] == "persona_x.safetensors"
    assert adapter["recorded_base"] is None
