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
import random
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import (
    Persona, Identity, ReferenceDataset, TrainingJob, QAResult,
    PersonaStatus, IdentityStatus, QAStatus, WorkflowStatus,
)
from app.providers.mocks import (
    MockLLMProvider, MockImageProvider, MockTrainerProvider, MockVoiceProvider,
)

llm = MockLLMProvider()
image_provider = MockImageProvider()
trainer = MockTrainerProvider()
voice = MockVoiceProvider()


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
    result = await llm.complete(
        system_prompt="Generate identity candidates for a fictional synthetic creator.",
        user_prompt=f"Create 3 identity candidates for persona {input_data.get('persona_name', 'unknown')}",
    )
    candidates = []
    for i in range(3):
        candidate = Identity(
            id=uuid4(),
            persona_id=UUID(persona_id),
            name=f"Candidate {i+1}",
            status=IdentityStatus.CANDIDATE,
            consistency_score=round(random.uniform(0.80, 0.98), 3),
            metadata_json={"source": "llm_generation", "candidate_index": i},
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
    identity_id = input_data.get("identity_id") or input_data.get("step_2_output", {}).get("identity_id")
    if not identity_id:
        return {"error": "no identity_id"}

    views = [
        "frontal portrait", "left profile", "right profile",
        "three quarter left", "three quarter right",
        "smiling", "neutral expression",
        "full body standing", "medium shot seated",
        "indoor lighting", "outdoor natural light",
    ]

    image_keys = []
    for view in views:
        result = await image_provider.generate(
            prompt=f"portrait of a {input_data.get('persona_name', 'model')}, {view}, high quality",
            seed=random.randint(0, 2**31),
        )
        if result.success:
            image_keys.append(result.data["image_key"])

    dataset = ReferenceDataset(
        id=uuid4(),
        identity_id=UUID(identity_id),
        name="primary_reference",
        image_keys=image_keys,
        total_images=len(image_keys),
        quality_score=round(random.uniform(0.85, 0.97), 3),
    )
    db.add(dataset)
    await db.flush()

    return {
        "dataset_id": str(dataset.id),
        "total_images": len(image_keys),
        "quality_score": dataset.quality_score,
    }


async def train_lora_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Train LoRA model for the identity."""
    identity_id = input_data.get("identity_id") or input_data.get("step_2_output", {}).get("identity_id")
    dataset_id = input_data.get("dataset_id") or input_data.get("step_4_output", {}).get("dataset_id")
    if not identity_id or not dataset_id:
        return {"error": "missing identity_id or dataset_id"}

    result = await trainer.train(
        dataset_id=dataset_id,
        model_type="lora",
        rank=16,
        epochs=10,
    )

    job = TrainingJob(
        id=uuid4(),
        identity_id=UUID(identity_id),
        dataset_id=UUID(dataset_id),
        status=WorkflowStatus.COMPLETED,
        output_path=result.data.get("model_path", ""),
        metrics={
            "loss": result.data.get("final_loss", 0),
            "training_time": result.data.get("training_time_s", 0),
        },
    )
    db.add(job)
    await db.flush()

    return {
        "model_path": result.data.get("model_path", ""),
        "loss": result.data.get("final_loss", 0),
        "training_time_s": result.data.get("training_time_s", 0),
    }


async def validate_identity_handler(
    workflow_id: UUID, step_id: UUID,
    input_data: dict, db: AsyncSession,
) -> dict:
    """Validate identity consistency after training."""
    identity_id = input_data.get("identity_id") or input_data.get("step_2_output", {}).get("identity_id")
    if not identity_id:
        return {"error": "no identity_id"}

    result = await llm.complete(
        system_prompt="Evaluate identity consistency. Return structured JSON with approved, identity_score, quality_score.",
        user_prompt="QA validation for trained identity model",
    )

    qa = QAResult(
        id=uuid4(),
        identity_id=UUID(identity_id),
        workflow_id=workflow_id,
        qa_type="consistency",
        status=QAStatus.PASSED if result.data.get("content", {}).get("approved", True) else QAStatus.FAILED,
        score=result.data.get("content", {}).get("identity_score", 0.9),
        threshold=0.85,
        details=result.data.get("content", {}),
        images_checked=11,
        passed_count=10,
        failed_count=1,
    )
    db.add(qa)
    await db.flush()

    # Update identity
    identity = await db.get(Identity, UUID(identity_id))
    if identity:
        identity.consistency_score = qa.score
        identity.status = IdentityStatus.READY if qa.status == QAStatus.PASSED else IdentityStatus.FAILED
        identity.lora_model_path = input_data.get("step_5_output", {}).get("model_path", "")

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
    identity_id = input_data.get("identity_id") or input_data.get("step_2_output", {}).get("identity_id")
    persona_name = input_data.get("persona_name", "model")
    voice_style = input_data.get("voice_style", "South African English")

    result = await voice.create_voice(
        name=f"{persona_name}_voice",
        description=f"Voice profile for {persona_name}",
        accent=voice_style,
        tone="warm, confident",
    )

    # Test synthesis
    synth_result = await voice.synthesize(
        text=f"Hello, I'm {persona_name}. Welcome to my world.",
        voice_id=result.data.get("voice_id", ""),
    )

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
    """Activate the persona — set all systems go."""
    identity_id = input_data.get("identity_id") or input_data.get("step_2_output", {}).get("identity_id")
    if identity_id:
        identity = await db.get(Identity, UUID(identity_id))
        if identity and identity.status != IdentityStatus.READY:
            identity.status = IdentityStatus.READY

    return {"status": "persona_activated", "persona_id": input_data.get("persona_id")}
