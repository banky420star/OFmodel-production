"""Persona Studio — Persona creation workflow.

Flow:
    create_persona
    → generate_identity_candidates
    → human_approval
    → build_reference_dataset
    → train_lora
    → validate_identity
    → create_voice
    → activate_persona
"""

from __future__ import annotations
import json
import random
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import (
    Persona, Identity, IdentityLock, ReferenceDataset, TrainingJob, QAResult,
    PersonaStatus, IdentityStatus, IdentityLockStatus, QAStatus, WorkflowStatus,
    persona_storage_hex, ensure_identity_lock,
)
from app.providers.registry import get_registry


def _get_llm():
    return get_registry().get_llm_provider()


def _get_image():
    return get_registry().get_image_provider()


def _get_trainer():
    return get_registry().get_trainer_provider()


def _get_voice():
    return get_registry().get_voice_provider()


async def create_persona_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Create the persona record."""
    persona = Persona(
        id=uuid4(),
        name=input_data["name"],
        age=input_data.get("age", 24),
        description=input_data.get("description", ""),
        status=PersonaStatus.ACTIVE,
        metadata_json={
            "appearance": input_data.get("appearance", {}),
            "personality": input_data.get("personality", []),
            "brand": input_data.get("brand", "luxury lifestyle"),
            "voice_style": input_data.get("voice_style", ""),
            "publishing_frequency": input_data.get("publishing_frequency", ""),
            "adult_verified": input_data.get("adult_verified", True),
            "synthetic_identity": input_data.get("synthetic_identity", True),
        },
    )
    db.add(persona)
    await db.flush()
    return {"persona_id": str(persona.id), "name": persona.name}


async def generate_candidates_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Generate identity candidates using LLM."""
    persona_id = input_data["persona_id"]
    persona_name = input_data.get('persona_name', 'unknown')
    brand = input_data.get('brand', 'lifestyle')
    age = input_data.get('age', 24)
    appearance = input_data.get('appearance', {})
    personality = input_data.get('personality', '')

    llm = _get_llm()
    schema = {
        "candidates": [
            {
                "name": "string",
                "appearance": "detailed physical description",
                "personality": "comma-separated traits",
                "consistency_score": 0.95
            }
        ]
    }
    system_prompt = (
        "You are a creative AI director for a synthetic persona studio. "
        "Generate realistic, detailed identity candidates for virtual influencer personas. "
        "Each candidate must have a unique look that fits the brand."
    )
    user_prompt = (
        f"Generate 3 identity candidates for persona '{persona_name}', age {age}, "
        f"brand: {brand}. Personality: {personality}. "
        f"Appearance details: {appearance}. "
        f"Each candidate needs: name, detailed physical appearance (hair, eyes, skin, build, style), "
        f"personality traits (comma-separated), and consistency_score (0.80-0.99)."
    )
    result = await llm.complete(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=schema,
    )

    # Parse LLM output into identity candidates
    candidates = []
    llm_data = result.data.get("content", {}) if result.success else {}
    raw_candidates = llm_data.get("candidates", []) if isinstance(llm_data, dict) else []

    for i in range(max(len(raw_candidates), 3)):
        if i < len(raw_candidates):
            c = raw_candidates[i]
            candidate_name = c.get("name", f"Candidate {i+1}")
            appearance_desc = c.get("appearance", "")
            personality_desc = c.get("personality", "")
            score = float(c.get("consistency_score", 0.90))
        else:
            candidate_name = f"Candidate {i+1}"
            appearance_desc = ""
            personality_desc = ""
            score = round(random.uniform(0.80, 0.95), 3)

        candidate = Identity(
            id=uuid4(),
            persona_id=UUID(persona_id),
            name=candidate_name,
            status=IdentityStatus.CANDIDATE,
            consistency_score=score,
            metadata_json={
                "source": "ollama" if result.success else "fallback",
                "candidate_index": i,
                "appearance": appearance_desc,
                "personality": personality_desc,
                "model": result.data.get("model", "unknown") if result.success else "none",
            },
        )
        db.add(candidate)
        candidates.append(str(candidate.id))
    await db.flush()
    return {"candidates": candidates, "count": len(candidates)}


async def approve_identity_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Approve the selected identity candidate."""
    identity_id = input_data.get("identity_id") or input_data.get("selected_identity")
    if not identity_id:
        # Auto-select the highest-scoring candidate
        result = await db.execute(
            select(Identity)
            .where(Identity.persona_id == UUID(input_data["persona_id"]))
            .where(Identity.status == IdentityStatus.CANDIDATE)
            .order_by(Identity.consistency_score.desc())
            .limit(1)
        )
        identity = result.scalar_one_or_none()
        if identity:
            identity_id = str(identity.id)

    if identity_id:
        identity = await db.get(Identity, UUID(identity_id))
        if identity:
            identity.status = IdentityStatus.APPROVED
            await db.flush()
            return {"identity_id": str(identity.id), "status": "approved"}

    return {"identity_id": None, "status": "no_candidates"}


async def build_reference_dataset_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Generate reference images for the identity."""
    # Find persona_id and identity_id from input or any previous step output
    persona_id = input_data.get("persona_id")
    identity_id = input_data.get("identity_id")
    if not identity_id:
        for key in sorted(input_data.keys()):
            if key.startswith("step_") and isinstance(input_data[key], dict):
                if "identity_id" in input_data[key]:
                    identity_id = input_data[key]["identity_id"]
                    break
    if not identity_id:
        return {"error": "no identity_id"}
    if not persona_id:
        return {"error": "no persona_id"}

    # Create the durable identity lock for this persona through the async ORM
    # so Auto-Produce, gallery, and the sync image engine all agree on one row.
    persona = await db.get(Persona, UUID(persona_id))
    lock = await ensure_identity_lock(persona, db, identity_id=UUID(identity_id))
    storage_hex = persona_storage_hex(persona.id)

    # Generate reference images through the identity engine using the same
    # full-hex key the rest of the system uses.
    from app.identity_engine import generate_identity_locked, stable_seed
    from pathlib import Path as _Path

    persona_name = input_data.get("persona_name", "model")

    # The edit-based provider needs an avatar as its identity reference.
    # Bootstrap a real one with a text-to-image call through the configured
    # image provider — no placeholder can enter storage/avatars/. On failure
    # the step fails and the identity lock never becomes ACTIVE.
    avatar_dir = _Path(__file__).parent.parent.parent / "storage" / "avatars"
    avatar_dir.mkdir(parents=True, exist_ok=True)
    avatar_path = avatar_dir / f"{persona.name.lower()}.jpg"
    if not avatar_path.exists():
        appearance = (getattr(persona, "appearance", None) or {}) or {}
        appearance_bits = ", ".join(
            f"{k.replace('_', ' ')}: {v}" for k, v in appearance.items()
        )
        avatar_prompt = (
            f"Professional portrait photograph of {persona.name}. "
            f"{persona.description or ''}. "
            f"{appearance_bits}. Frontal head-and-shoulders portrait, "
            "neutral studio background, soft even lighting, photorealistic"
        )
        avatar_result = await _get_image().generate(
            prompt=avatar_prompt,
            width=768,
            height=768,
            seed=stable_seed("avatar", str(persona.id)),
        )
        if not avatar_result.success or not avatar_result.data.get("image_bytes"):
            raise RuntimeError(
                f"Avatar bootstrap failed for {persona.name}: "
                f"{avatar_result.error or 'image provider returned no image'}"
            )
        avatar_path.write_bytes(avatar_result.data["image_bytes"])
        logger_info = f"generated real avatar for {persona.name} via {avatar_result.provider}"
    else:
        logger_info = "existing avatar used as identity reference"

    views = [
        "frontal portrait, neutral expression, studio lighting",
        "left profile, natural light",
        "right profile, soft window light",
        "three quarter view, warm smile",
        "three quarter view, confident expression",
        "full body standing, fashion pose",
        "medium shot seated, relaxed",
        "indoor lighting, cozy setting",
        "outdoor natural light, golden hour",
    ]

    dataset_dir = _Path(__file__).parent.parent.parent / "storage" / "datasets" / storage_hex[:8]
    dataset_dir.mkdir(parents=True, exist_ok=True)

    image_keys = []
    gen_errors = []
    for i, view in enumerate(views):
        out_path = str(dataset_dir / f"ref_{i+1:02d}.png")
        result = await generate_identity_locked(
            persona_id_hex=storage_hex,
            scene_prompt=view,
            output_path=out_path,
            seed_override=stable_seed(identity_id, i),
        )
        if result["success"]:
            image_keys.append(out_path)
        else:
            gen_errors.append(result.get("error", "unknown"))

    dataset = ReferenceDataset(
        id=uuid4(),
        identity_id=UUID(identity_id),
        name="primary_reference",
        image_keys=image_keys,
        total_images=len(image_keys),
        quality_score=None,
    )
    db.add(dataset)
    await db.flush()

    return {
        "dataset_id": str(dataset.id),
        "total_images": len(image_keys),
        "quality_score": None,
        "coverage_score": round(len(image_keys) / len(views), 3),
        "identity_lock_status": lock.status,
        "avatar": logger_info,
        "errors": gen_errors[:1],
    }


async def train_lora_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Train LoRA model for the identity."""
    # Find identity_id and dataset_id from any previous step output
    identity_id = input_data.get("identity_id")
    dataset_id = input_data.get("dataset_id")
    if not identity_id or not dataset_id:
        for key in sorted(input_data.keys()):
            if key.startswith("step_") and isinstance(input_data[key], dict):
                val = input_data[key]
                if not identity_id and "identity_id" in val:
                    identity_id = val["identity_id"]
                if not dataset_id and "dataset_id" in val:
                    dataset_id = val["dataset_id"]
    if not identity_id or not dataset_id:
        return {"error": "missing identity_id or dataset_id"}

    tr = _get_trainer()
    result = await tr.train(
        dataset_id=dataset_id,
        model_type="lora",
        rank=16,
        epochs=10,
    )

    # Record the training attempt truthfully even when the trainer fails —
    # a missing GPU must not corrupt the identity pipeline, but it must not
    # be hidden either.
    job = TrainingJob(
        id=uuid4(),
        identity_id=UUID(identity_id),
        dataset_id=UUID(dataset_id),
        status=WorkflowStatus.COMPLETED if result.success else WorkflowStatus.FAILED,
        output_path=result.data.get("model_path", "") if result.success else "",
        metrics={
            "loss": result.data.get("final_loss", 0),
            "training_time": result.data.get("training_time_s", 0),
            "provider": result.provider,
            **({"error": result.error} if not result.success else {}),
        },
    )
    db.add(job)
    await db.flush()

    return {
        "model_path": result.data.get("model_path", "") if result.success else "",
        "loss": result.data.get("final_loss", 0),
        "training_time_s": result.data.get("training_time_s", 0),
        "provider": result.provider,
        "training_failed": not result.success,
        "training_error": "" if result.success else result.error,
    }


async def validate_identity_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Validate identity consistency after training."""
    # Find identity_id from any previous step output
    identity_id = input_data.get("identity_id")
    if not identity_id:
        for key in sorted(input_data.keys()):
            if key.startswith("step_") and isinstance(input_data[key], dict):
                if "identity_id" in input_data[key]:
                    identity_id = input_data[key]["identity_id"]
                    break
    if not identity_id:
        return {"error": "no identity_id"}

    llm2 = _get_llm()
    result = await llm2.complete(
        system_prompt="Evaluate identity consistency. Return structured JSON with approved, identity_score, quality_score.",
        user_prompt="QA validation for trained identity model",
    )

    content = result.data.get("content", {}) if isinstance(result.data, dict) else {}
    required_fields = {"approved", "identity_score", "quality_score"}
    evaluator_ready = isinstance(content, dict) and required_fields.issubset(content)
    if not evaluator_ready:
        content = {
            "evaluation_status": "blocked",
            "error": "Evaluator did not return approved, identity_score, and quality_score.",
            "raw_response": content,
        }

    qa = QAResult(
        id=uuid4(),
        identity_id=UUID(identity_id),
        workflow_id=workflow_id,
        qa_type="consistency",
        status=QAStatus.PASSED if evaluator_ready and content["approved"] else QAStatus.FAILED,
        score=float(content["identity_score"]) if evaluator_ready else 0.0,
        threshold=0.85,
        details=content,
        images_checked=int(content.get("images_checked", 0)),
        passed_count=int(content.get("passed_count", 0)),
        failed_count=int(content.get("failed_count", 0)),
    )
    db.add(qa)
    await db.flush()

    # Update identity
    identity = await db.get(Identity, UUID(identity_id))
    if identity:
        identity.consistency_score = qa.score
        identity.status = IdentityStatus.READY if qa.status == QAStatus.PASSED else IdentityStatus.FAILED
        if identity.lora_model_path == "":
            for key in sorted(input_data.keys()):
                if key.startswith("step_") and isinstance(input_data[key], dict):
                    if input_data[key].get("model_path"):
                        identity.lora_model_path = input_data[key]["model_path"]
                        break

    return {
        "approved": qa.status == QAStatus.PASSED,
        "identity_score": qa.score,
        "qa_id": str(qa.id),
    }


async def create_voice_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Create voice profile for the identity."""
    identity_id = input_data.get("identity_id")
    if not identity_id:
        for key in sorted(input_data.keys()):
            if key.startswith("step_") and isinstance(input_data[key], dict):
                if "identity_id" in input_data[key]:
                    identity_id = input_data[key]["identity_id"]
                    break
    persona_name = input_data.get("persona_name", "model")
    voice_style = input_data.get("voice_style", "South African English")

    voc = _get_voice()
    result = await voc.create_voice(
        name=f"{persona_name}_voice",
        description=f"Voice profile for {persona_name}",
        accent=voice_style,
        tone="warm, confident",
    )

    if not result.success:
        return {
            "voice_id": "",
            "voice_key": "",
            "duration_seconds": 0,
            "status": "voice_unavailable",
            "voice_error": result.error,
        }

    # Test synthesis
    synth_result = await voc.synthesize(
        text=f"Hello, I'm {persona_name}. Welcome to my world.",
        voice_id=result.data.get("voice_id", ""),
    )

    if not synth_result.success:
        return {
            "voice_id": result.data.get("voice_id", ""),
            "voice_key": "",
            "duration_seconds": 0,
            "status": "voice_unavailable",
            "voice_error": synth_result.error,
        }

    return {
        "voice_id": result.data.get("voice_id", ""),
        "voice_key": synth_result.data.get("voice_key", ""),
        "duration_seconds": synth_result.data.get("duration_seconds", 0),
        "status": "ready",
    }


async def activate_persona_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Activate the persona and finalize its identity lock."""
    persona_id = input_data.get("persona_id")
    identity_id = input_data.get("identity_id")
    if not identity_id:
        for key in sorted(input_data.keys()):
            if key.startswith("step_") and isinstance(input_data[key], dict):
                if "identity_id" in input_data[key]:
                    identity_id = input_data[key]["identity_id"]
                    break

    persona = await db.get(Persona, UUID(persona_id))
    if not persona:
        return {"status": "persona_not_found", "persona_id": persona_id}

    if identity_id:
        identity = await db.get(Identity, UUID(identity_id))
        if identity and identity.status != IdentityStatus.READY:
            identity.status = IdentityStatus.READY
            await db.flush()

    # Create or refresh the durable identity lock for this persona.
    # This is the missing step that made new personas fail with
    # "No identity lock for persona" during Auto-Produce.
    lock = await ensure_identity_lock(persona, db, identity_id=identity_id)

    return {
        "status": "persona_activated",
        "persona_id": persona_id,
        "identity_lock_id": lock.persona_id,
        "identity_lock_status": lock.status,
    }
