"""Persona Studio — LoRA reference-image resolution.

Regression for the bug that made every build fail after ~40 minutes of
successful reference generation:

    build_reference_dataset wrote storage/datasets/<persona_hex8>/ref_NN.png
    and recorded those absolute paths in ReferenceDataset.image_keys, but
    hf_trainer resolved images from storage/datasets/<dataset_id>/ and ignored
    image_keys — a directory that never exists. Every training run therefore
    raised "No reference images found", the build reported all steps COMPLETED
    anyway, and the identity was promoted to READY with no LoRA behind it.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.models import Identity, IdentityStatus, Persona, ReferenceDataset
from app.providers.base import ProviderResult
from app.providers.hf_trainer import HuggingFaceTrainer

from tests.fakes import FakeTrainerProvider


def _write_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
    return path


# ── resolution ───────────────────────────────────────────────────────

def test_resolves_recorded_paths(tmp_path):
    keys = [str(_write_png(tmp_path / f"ref_{i:02d}.png")) for i in range(1, 10)]
    found = HuggingFaceTrainer.resolve_dataset_images("ds-1", keys)
    assert len(found) == 9
    assert {p.name for p in found} == {f"ref_{i:02d}.png" for i in range(1, 10)}


def test_recorded_but_missing_reports_count_and_first_path(tmp_path):
    keys = [str(tmp_path / "gone_01.png"), str(tmp_path / "gone_02.png")]
    with pytest.raises(ValueError) as exc:
        HuggingFaceTrainer.resolve_dataset_images("ds-2", keys)
    message = str(exc.value)
    assert "records 2 reference image(s)" in message
    assert "gone_01.png" in message


def test_partially_missing_still_trains_on_what_exists(tmp_path):
    present = _write_png(tmp_path / "ref_01.png")
    keys = [str(present), str(tmp_path / "ref_02.png")]
    found = HuggingFaceTrainer.resolve_dataset_images("ds-3", keys)
    assert found == [present]


def test_falls_back_to_directory_for_legacy_datasets(tmp_path, monkeypatch):
    """Datasets recorded by an older build stored images under the dataset id."""
    import app.providers.hf_trainer as trainer_module

    monkeypatch.setattr(trainer_module, "DATASETS_DIR", tmp_path)
    _write_png(tmp_path / "legacy-ds" / "a.png")
    _write_png(tmp_path / "legacy-ds" / "b.jpg")

    found = HuggingFaceTrainer.resolve_dataset_images("legacy-ds", None)
    assert len(found) == 2


def test_no_paths_and_no_directory_raises(tmp_path, monkeypatch):
    import app.providers.hf_trainer as trainer_module

    monkeypatch.setattr(trainer_module, "DATASETS_DIR", tmp_path)
    with pytest.raises(ValueError) as exc:
        HuggingFaceTrainer.resolve_dataset_images("absent-ds", None)
    assert "No reference images found" in str(exc.value)
    assert "absent-ds" in str(exc.value)


def test_empty_recorded_list_uses_directory_fallback(tmp_path, monkeypatch):
    """An empty list is falsy, so it must not be treated as an authoritative
    'no images' — it means the record has nothing to say."""
    import app.providers.hf_trainer as trainer_module

    monkeypatch.setattr(trainer_module, "DATASETS_DIR", tmp_path)
    _write_png(tmp_path / "ds-empty" / "only.png")
    found = HuggingFaceTrainer.resolve_dataset_images("ds-empty", [])
    assert len(found) == 1


# ── the build step must hand the trainer the dataset's own images ────

class _FailingTrainer(FakeTrainerProvider):
    async def train(self, dataset_id, model_type="lora", rank=16, epochs=10,
                    learning_rate=1e-4, batch_size=4, image_paths=None):
        self.calls.append({"dataset_id": dataset_id, "image_paths": image_paths})
        return ProviderResult(
            False, {"model_path": ""},
            error="No reference images found for dataset 'x'.",
            provider="failing_trainer",
        )


async def _seed_persona_identity_dataset(db, image_keys: list[str]):
    persona = Persona(name=f"LoRA_{uuid.uuid4().hex[:6]}", age=24)
    db.add(persona)
    await db.flush()
    identity = Identity(
        id=uuid.uuid4(), persona_id=persona.id, name="cand",
        status=IdentityStatus.APPROVED,
    )
    db.add(identity)
    await db.flush()
    dataset = ReferenceDataset(
        id=uuid.uuid4(), identity_id=identity.id, name="primary_reference",
        image_keys=image_keys, total_images=len(image_keys),
    )
    db.add(dataset)
    await db.flush()
    return identity, dataset


@pytest.mark.asyncio
async def test_train_handler_passes_dataset_image_keys(db, registry_override):
    from app.workflows.persona_flow import train_lora_handler

    keys = ["/tmp/datasets/abc/ref_01.png", "/tmp/datasets/abc/ref_02.png"]
    identity, dataset = await _seed_persona_identity_dataset(db, keys)
    trainer = registry_override("trainer", FakeTrainerProvider())

    result = await train_lora_handler(
        workflow_id=uuid.uuid4(), step_id=uuid.uuid4(),
        input_data={"identity_id": str(identity.id), "dataset_id": str(dataset.id)},
        db=db,
    )

    assert result["training_failed"] is False
    assert trainer.calls, "trainer was never called"
    assert trainer.calls[0]["image_paths"] == keys, (
        "the build must hand the trainer the dataset's recorded images; "
        "without them it guesses a directory that does not exist"
    )


@pytest.mark.asyncio
async def test_missing_dataset_fails_instead_of_training_nothing(db, registry_override):
    from app.workflows.persona_flow import train_lora_handler

    identity, _ = await _seed_persona_identity_dataset(db, [])
    trainer = registry_override("trainer", FakeTrainerProvider())

    with pytest.raises(RuntimeError) as exc:
        await train_lora_handler(
            workflow_id=uuid.uuid4(), step_id=uuid.uuid4(),
            input_data={"identity_id": str(identity.id), "dataset_id": str(uuid.uuid4())},
            db=db,
        )
    assert "does not exist" in str(exc.value)
    assert trainer.calls == [], "must not call the trainer with no dataset"


@pytest.mark.asyncio
async def test_dataset_with_no_images_fails_before_training(db, registry_override):
    from app.workflows.persona_flow import train_lora_handler

    identity, dataset = await _seed_persona_identity_dataset(db, [])
    trainer = registry_override("trainer", FakeTrainerProvider())

    with pytest.raises(RuntimeError) as exc:
        await train_lora_handler(
            workflow_id=uuid.uuid4(), step_id=uuid.uuid4(),
            input_data={"identity_id": str(identity.id), "dataset_id": str(dataset.id)},
            db=db,
        )
    assert "records no images" in str(exc.value)
    assert trainer.calls == []


@pytest.mark.asyncio
async def test_failed_training_is_recorded_but_does_not_stop_the_build(db, registry_override):
    """A failed training activates with a warning, it does not abort the build.

    Two wrong answers came before this one. Returning normally with
    training_failed=True was invisible: the step was marked COMPLETED, the
    identity went READY with a defaulted consistency score, and the persona went
    ACTIVE with no model behind it. Raising instead was loud but destructive: one
    missing GPU or one OOM left the persona stuck in BUILDING, unusable, with the
    reason buried in a log. Neither is acceptable, so the failure is now put on
    the record and the build continues.
    """
    from app.workflows.persona_flow import train_lora_handler

    identity, dataset = await _seed_persona_identity_dataset(db, ["/tmp/nope/ref_01.png"])
    persona = await db.get(Persona, identity.persona_id)
    registry_override("trainer", _FailingTrainer())

    result = await train_lora_handler(
        workflow_id=uuid.uuid4(), step_id=uuid.uuid4(),
        input_data={
            "persona_id": str(identity.persona_id),
            "identity_id": str(identity.id),
            "dataset_id": str(dataset.id),
        },
        db=db,
    )

    # 1. The step reports the failure rather than pretending it succeeded.
    assert result["training_failed"] is True
    assert result["warnings"], "the failure must be visible in the step output"
    assert not result["model_path"], "no adapter was produced, so no path may be claimed"

    # 2. The attempt is recorded truthfully, so the operator can see why.
    from sqlalchemy import select
    from app.models import TrainingJob, WorkflowStatus

    jobs = (await db.execute(
        select(TrainingJob).where(TrainingJob.dataset_id == dataset.id)
    )).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].status == WorkflowStatus.FAILED
    assert jobs[0].metrics.get("error")

    # 3. A REVIEW QA row carries the verdict, so it is visible as QA, not a log line.
    from app.models import QAResult, QAStatus

    qa_rows = (await db.execute(
        select(QAResult).where(QAResult.identity_id == identity.id)
    )).scalars().all()
    training_rows = [row for row in qa_rows if row.qa_type == "training"]
    assert len(training_rows) == 1
    assert training_rows[0].status == QAStatus.REVIEW
    assert training_rows[0].details.get("error")

    # 4. ...and the persona record itself carries the warning.
    await db.refresh(persona)
    warnings = (persona.metadata_json or {}).get("warnings") or []
    assert any("LoRA training failed" in w["message"] for w in warnings)


# ── the LoRA base model must match the image checkpoint ──────────────

def test_checkpoint_to_base_model_mapping():
    from app.providers.hf_trainer import base_model_for_checkpoint

    assert base_model_for_checkpoint("sd_xl_base_1.0.safetensors") == (
        "stabilityai/stable-diffusion-xl-base-1.0"
    )
    assert base_model_for_checkpoint("v1-5-pruned-emaonly.safetensors") == (
        "runwayml/stable-diffusion-v1-5"
    )
    assert base_model_for_checkpoint("mystery-model.safetensors") == ""


def test_trainer_follows_the_image_checkpoint(monkeypatch):
    """Default base model is derived, so the adapter can only be applied to the
    checkpoint that actually generates the images."""
    from app.config import get_settings
    from app.providers.hf_trainer import HuggingFaceTrainer

    settings = get_settings()
    monkeypatch.setattr(settings, "TRAINER_BASE_MODEL", "", raising=False)
    monkeypatch.setattr(
        settings, "COMFYUI_CHECKPOINT", "sd_xl_base_1.0.safetensors", raising=False
    )
    trainer = HuggingFaceTrainer()
    assert "xl" in trainer._base_model.casefold()
    trainer.assert_matches_image_checkpoint()  # must not raise


def test_explicit_base_model_is_not_overridden(monkeypatch):
    from app.config import get_settings
    from app.providers.hf_trainer import HuggingFaceTrainer

    monkeypatch.setattr(
        get_settings(), "COMFYUI_CHECKPOINT", "sd_xl_base_1.0.safetensors", raising=False
    )
    trainer = HuggingFaceTrainer(base_model="runwayml/stable-diffusion-v1-5")
    assert trainer._base_model == "runwayml/stable-diffusion-v1-5"


def test_mismatched_base_model_is_refused(monkeypatch):
    """An SD 1.5 LoRA cannot be applied to an SDXL checkpoint. Refuse before the
    multi-gigabyte download, not after hours of training."""
    from app.config import get_settings
    from app.providers.hf_trainer import HuggingFaceTrainer

    settings = get_settings()
    monkeypatch.setattr(settings, "IMAGE_PROVIDER", "comfyui", raising=False)
    monkeypatch.setattr(
        settings, "COMFYUI_CHECKPOINT", "sd_xl_base_1.0.safetensors", raising=False
    )
    trainer = HuggingFaceTrainer(base_model="runwayml/stable-diffusion-v1-5")

    with pytest.raises(ValueError) as exc:
        trainer.assert_matches_image_checkpoint()
    message = str(exc.value)
    assert "does not match" in message
    assert "TRAINER_BASE_MODEL" in message


def test_no_mismatch_check_when_comfyui_is_not_the_image_provider(monkeypatch):
    from app.config import get_settings
    from app.providers.hf_trainer import HuggingFaceTrainer

    settings = get_settings()
    monkeypatch.setattr(settings, "IMAGE_PROVIDER", "eachsense", raising=False)
    trainer = HuggingFaceTrainer(base_model="runwayml/stable-diffusion-v1-5")
    trainer.assert_matches_image_checkpoint()  # must not raise
