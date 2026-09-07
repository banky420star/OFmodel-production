"""Persona Studio — persona routes."""

from __future__ import annotations
import json
import time
import random
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Depends, Query, Form
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    Persona, Identity, Workflow, GeneratedImage,
    PersonaStatus, IdentityStatus, WorkflowStatus,
)
from app.schemas import (
    PersonaCreate, PersonaResponse, IdentityResponse, WorkflowResponse,
)
from app.workflows.engine import workflow_engine
from app.workflows.persona_flow import (
    create_persona_handler, generate_candidates_handler,
    approve_identity_handler, build_reference_dataset_handler,
    train_lora_handler, validate_identity_handler,
    create_voice_handler, activate_persona_handler,
)

router = APIRouter()

# Persona CRUD (lines 203-315)
@router.get("/personas", response_model=list[PersonaResponse])
async def list_personas(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(Persona).order_by(Persona.created_at.desc())
    if status:
        q = q.where(Persona.status == status)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/personas", response_model=PersonaResponse, status_code=201)
async def create_persona(body: PersonaCreate, db: AsyncSession = Depends(get_db)):
    persona = Persona(
        id=uuid4(),
        name=body.name,
        status=PersonaStatus.BUILDING,
        appearance=body.appearance.model_dump() if body.appearance else None,
        personality=body.personality,
        voice_style=body.voice_style,
        brand=body.brand,
        publishing_frequency=body.publishing_frequency,
        metadata_json={},
    )
    db.add(persona)
    await db.commit()
    await db.refresh(persona)

    # Run the workflow in the background
    import asyncio
    asyncio.create_task(_run_persona_workflow(persona.id))

    return persona


async def _run_persona_workflow(persona_id: UUID):
    """Run the persona creation workflow in the background."""
    from app.database import AsyncSessionLocal
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        persona = await db.get(Persona, persona_id)
        if not persona:
            return

        input_data = {
            "persona_id": str(persona_id),
            "persona_name": persona.name,
            "name": persona.name,
            "age": persona.age,
            "appearance": persona.appearance or {},
            "personality": persona.personality or [],
            "voice_style": persona.voice_style or "",
            "brand": persona.brand or "",
            "publishing_frequency": persona.publishing_frequency or "",
            "adult_verified": True,
            "synthetic_identity": True,
        }

        workflow = await workflow_engine.create_workflow(
            name=f"persona_creation_{persona.name}",
            workflow_type="persona_creation",
            persona_id=persona_id,
            input_data=input_data,
            steps=[
                {"name": "generate_candidates", "step_type": "generate_candidates"},
                {"name": "approve_identity", "step_type": "approve_identity"},
                {"name": "build_reference_dataset", "step_type": "build_reference_dataset"},
                {"name": "train_lora", "step_type": "train_lora"},
                {"name": "validate_identity", "step_type": "validate_identity"},
                {"name": "create_voice", "step_type": "create_voice"},
                {"name": "activate_persona", "step_type": "activate_persona"},
            ],
        )

    # Register step handlers
    workflow_engine.register_step("generate_candidates", generate_candidates_handler)
    workflow_engine.register_step("approve_identity", approve_identity_handler)
    workflow_engine.register_step("build_reference_dataset", build_reference_dataset_handler)
    workflow_engine.register_step("train_lora", train_lora_handler)
    workflow_engine.register_step("validate_identity", validate_identity_handler)
    workflow_engine.register_step("create_voice", create_voice_handler)
    workflow_engine.register_step("activate_persona", activate_persona_handler)

    # Run workflow (opens its own session)
    await workflow_engine.run_workflow(workflow.id)

    # Update persona status after workflow completes
    async with AsyncSessionLocal() as db:
        persona = await db.get(Persona, persona_id)
        if persona and persona.status == PersonaStatus.BUILDING:
            persona.status = PersonaStatus.ACTIVE
            await db.commit()


@router.get("/personas/{persona_id}", response_model=PersonaResponse)
async def get_persona(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    return persona


# ─── Analytics (Phase 11) ────────────────────────────────────────────

class ManualAnalyticsInput(BaseModel):
    followers: int = 0
    engagement_rate: float = 0.0
    revenue: float = 0.0
    platform: str = "instagram"



@router.get("/personas/{persona_id}/identities", response_model=list[IdentityResponse])
async def list_identities(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Identity).where(Identity.persona_id == persona_id).order_by(Identity.created_at.desc())
    )
    return result.scalars().all()


@router.post("/personas/{persona_id}/identities/{identity_id}/approve")
async def approve_identity(persona_id: UUID, identity_id: UUID, db: AsyncSession = Depends(get_db)):
    identity = await db.get(Identity, identity_id)
    if not identity or identity.persona_id != persona_id:
        raise HTTPException(404, "Identity not found")

    # Deactivate other identities
    others = await db.execute(
        select(Identity).where(Identity.persona_id == persona_id, Identity.id != identity_id)
    )
    for other in others.scalars().all():
        other.status = IdentityStatus.REJECTED

    identity.status = IdentityStatus.APPROVED
    await db.commit()
    return {"status": "approved", "identity_id": str(identity_id)}


# ─── Identity Lock (Consistent Identity) ─────────────────────────────

@router.get("/personas/{persona_id}/identity-lock")
async def get_identity_lock(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get the identity-lock seed and prompt for consistent image generation."""
    from sqlalchemy import text
    pid_hex = persona_id.hex
    result = await db.execute(
        text("SELECT seed, identity_prompt, negative_prompt, style_tags FROM identity_locks WHERE persona_id = :pid"),
        {"pid": pid_hex},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(404, "No identity lock for this persona")
    return {
        "persona_id": str(persona_id),
        "seed": row[0],
        "identity_prompt": row[1],
        "negative_prompt": row[2],
        "style_tags": row[3] if isinstance(row[3], list) else __import__('json').loads(row[3] or '[]'),
    }


@router.post("/personas/{persona_id}/generate-locked-image")
async def generate_locked_image(
    persona_id: UUID,
    scene_prompt: str = "",
    db: AsyncSession = Depends(get_db),
):
    """Generate an image using the identity-locked seed + prompt.
    
    Combines the identity base prompt with a scene prompt,
    using the locked seed to ensure the same face every time.
    """
    from sqlalchemy import text

    pid_hex = persona_id.hex
    result = await db.execute(
        text("SELECT seed, identity_prompt, negative_prompt FROM identity_locks WHERE persona_id = :pid"),
        {"pid": pid_hex},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(404, "No identity lock for this persona")

    # Use identity engine for consistent face generation
    from app.identity_engine import generate_identity_locked
    
    persona = await db.get(Persona, persona_id)
    pid_hex = persona_id.hex
    avatar_dir = Path(__file__).parent.parent / "storage" / "avatars"
    filename = f"{persona.name.lower()}_locked.png"
    output_path = str(avatar_dir / filename)
    
    result = generate_identity_locked(
        persona_id_hex=pid_hex,
        scene_prompt=scene_prompt or "portrait, natural lighting, photorealistic",
        output_path=output_path,
    )
    
    if not result["success"]:
        raise HTTPException(502, f"Generation failed: {result.get('error', 'unknown')}")
    
    return {
        "seed": result["seed"],
        "prompt": result["prompt"],
        "avatar_url": f"/api/v1/avatars/{filename}",
        "size_bytes": result["size_bytes"],
        "latency_ms": result["latency_ms"],
    }


# ─── Gallery ─────────────────────────────────────────────────────────

import glob as glob_mod


@router.get("/personas/{persona_id}/gallery")
async def get_persona_gallery(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get all gallery images for a persona (avatar + variations)."""
    from sqlalchemy import text
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    name_lower = persona.name.lower()
    gallery_dir = Path(__file__).parent.parent / "storage" / "gallery"
    avatar_dir = Path(__file__).parent.parent / "storage" / "avatars"

    images = []

    # Avatar (main profile)
    avatar_path = avatar_dir / f"{name_lower}.jpg"
    if avatar_path.exists():
        images.append({
            "url": f"/api/v1/avatars/{name_lower}.jpg",
            "label": "Profile",
            "type": "avatar",
        })

    # Gallery variations
    pattern = f"{name_lower}_*.png"
    for f in sorted(gallery_dir.glob(pattern)):
        label = f.stem.replace(f"{name_lower}_", "").replace("_", " ").title()
        images.append({
            "url": f"/api/v1/gallery/{f.name}",
            "label": label,
            "type": "variation",
        })

    return {
        "persona_id": str(persona_id),
        "name": persona.name,
        "count": len(images),
        "images": images,
    }


# ─── Shoots (Phase 4) ────────────────────────────────────────────────


@router.post("/personas/{persona_id}/autopilot")
async def toggle_autopilot(
    persona_id: UUID,
    mode: str = "off",
    db: AsyncSession = Depends(get_db),
):
    """Toggle autopilot mode for a persona."""
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    # Store mode in metadata
    if not persona.metadata_json:
        persona.metadata_json = {}
    persona.metadata_json["autopilot"] = mode
    await db.commit()

    return {"autopilot": mode, "persona_id": str(persona_id)}


# ─── Video Generation (Phase 8) ──────────────────────────────────────

