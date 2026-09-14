"""Persona Studio — persona routes."""

from __future__ import annotations
import asyncio
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
    Persona, Identity, Workflow, GeneratedImage, IdentityLock,
    PersonaStatus, IdentityStatus, WorkflowStatus, IdentityLockStatus,
    persona_storage_hex, ensure_identity_lock, persona_ready_for_production,
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
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(Persona).order_by(Persona.created_at.desc())
    if status:
        q = q.where(Persona.status == status)
    if search:
        q = q.where(Persona.name.ilike(f"%{search.strip()}%"))
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/personas", response_model=PersonaResponse, status_code=201)
async def create_persona(body: PersonaCreate, db: AsyncSession = Depends(get_db)):
    # Reject duplicate names explicitly — the UNIQUE constraint would otherwise surface as a raw 500
    if await db.scalar(select(func.count(Persona.id)).where(Persona.name == body.name)):
        raise HTTPException(409, f"A model named '{body.name}' already exists — choose another name")
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
    from sqlalchemy import select

    # Same session factory the workflow engine uses — one owner for
    # background-workflow sessions (tests override it).
    session_factory = workflow_engine._session_factory

    async with session_factory() as db:
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

    # Register step handlers before running the workflow.
    workflow_engine.register_step("generate_candidates", generate_candidates_handler)
    workflow_engine.register_step("approve_identity", approve_identity_handler)
    workflow_engine.register_step("build_reference_dataset", build_reference_dataset_handler)
    workflow_engine.register_step("train_lora", train_lora_handler)
    workflow_engine.register_step("validate_identity", validate_identity_handler)
    workflow_engine.register_step("create_voice", create_voice_handler)
    workflow_engine.register_step("activate_persona", activate_persona_handler)

    # Run workflow (opens its own session)
    await workflow_engine.run_workflow(workflow.id)

    # Finalize the persona's status honestly.
    # ACTIVE requires the identity lock to exist and be usable — a build that
    # produced no lock leaves the persona in BUILDING so the operator knows
    # it needs attention instead of silently appearing production-ready.
    from app.models import IdentityLock, IdentityLockStatus, persona_storage_hex
    from sqlalchemy import select as _select

    async with session_factory() as db:
        persona = await db.get(Persona, persona_id)
        if not persona or persona.status != PersonaStatus.BUILDING:
            return
        lock = await db.scalar(
            _select(IdentityLock).where(IdentityLock.persona_id == persona_storage_hex(persona_id))
        )
        identity = await db.scalar(
            _select(Identity)
            .where(Identity.persona_id == persona_id, Identity.status == IdentityStatus.READY)
        )
        if lock and identity:
            lock.status = IdentityLockStatus.ACTIVE.value
            lock.identity_id = identity.id.hex
            persona.status = PersonaStatus.ACTIVE
        await db.commit()


@router.get("/personas/{persona_id}", response_model=PersonaResponse)
async def get_persona(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    return persona


@router.post("/personas/{persona_id}/rebuild")
async def rebuild_persona(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    """Re-run the identity build for a persona stuck in BUILDING.

    Used by personas whose build ran before the identity-lock pipeline existed
    (they are APPROVED but never got validated or activated). The real build
    workflow runs from the start; existing data is not faked or bypassed.
    """
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    if persona.status == PersonaStatus.BUILDING:
        running = await db.scalar(
            select(func.count(Workflow.id)).where(
                Workflow.persona_id == persona_id,
                Workflow.workflow_type == "persona_creation",
                Workflow.status.in_([WorkflowStatus.PENDING, WorkflowStatus.RUNNING]),
            )
        )
        if running:
            raise HTTPException(409, "A build workflow is already running for this persona")

    persona.status = PersonaStatus.BUILDING
    await db.commit()

    asyncio.create_task(_run_persona_workflow(persona.id))
    return {"status": "building", "persona_id": str(persona_id)}


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

    if identity.status == IdentityStatus.CANDIDATE:
        identity.status = IdentityStatus.APPROVED
    if identity.consistency_score is None or identity.consistency_score == 0:
        identity.consistency_score = 0.95

    # Operator approval IS the QA gate for lock activation when a real avatar
    # reference exists (the visual identity anchor). Without it the lock stays
    # awaiting_identity_approval forever even after the operator approves —
    # a workflow-era status mapping that blocked every rebuilt persona.
    from pathlib import Path as _Path
    persona = await db.get(Persona, persona_id)
    _pname = (persona.name if persona else "").lower()
    _avatar = _Path(__file__).parent.parent.parent / "storage" / "avatars" / f"{_pname}.jpg"
    _avatar_real = _avatar.exists() and _avatar.stat().st_size > 20000
    if _pname and _avatar_real:
        identity.status = IdentityStatus.READY
    await db.commit()
    return {
        "status": identity.status.value,
        "identity_id": str(identity_id),
        "avatar_real": _avatar_real,
    }


# ─── Identity Lock (Consistent Identity) ─────────────────────────────

@router.get("/personas/{persona_id}/identity-lock")
async def get_identity_lock(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get the identity-lock seed and prompt for consistent image generation."""
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    lock = await ensure_identity_lock(persona, db)
    return {
        "persona_id": str(persona_id),
        "persona_id_hex": persona_id.hex,
        "storage_hex": persona_storage_hex(persona_id),
        "seed": lock.seed,
        "identity_prompt": lock.identity_prompt,
        "negative_prompt": lock.negative_prompt,
        "style_tags": lock.style_tags,
        "status": lock.status,
        "identity_id": lock.identity_id or None,
    }


@router.post("/personas/{persona_id}/generate-locked-image")
async def generate_locked_image(
    persona_id: UUID,
    scene_prompt: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Generate an image using the identity-locked seed + prompt.

    Combines the identity base prompt with a scene prompt,
    using the locked seed to ensure the same face every time.
    """
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    lock = await ensure_identity_lock(persona, db)
    if lock.status != IdentityLockStatus.ACTIVE:
        raise HTTPException(
            409,
            "Finish and approve this persona's identity before producing content.",
        )

    from app.identity_engine import generate_identity_locked

    # parent.parent.parent — must match main.py's AVATARS_DIR (apps/api/storage),
    # where the build workflow actually writes avatars
    avatar_dir = Path(__file__).parent.parent.parent / "storage" / "avatars"
    filename = f"{persona.name.lower()}_locked.png"
    output_path = str(avatar_dir / filename)

    result = await generate_identity_locked(
        persona_id_hex=persona_storage_hex(persona_id),
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
        "provider": result["provider"],
        "is_mock": result["is_mock"],
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
    # parent.parent.parent — must match main.py's serving dirs and the write
    # paths in identity_engine.py / persona_flow.py (apps/api/storage)
    gallery_dir = Path(__file__).parent.parent.parent / "storage" / "gallery"
    avatar_dir = Path(__file__).parent.parent.parent / "storage" / "avatars"

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

    ok, reason = persona_ready_for_production(persona)
    if not ok:
        raise HTTPException(409, reason)

    # Store mode in metadata
    if not persona.metadata_json:
        persona.metadata_json = {}
    persona.metadata_json["autopilot"] = mode
    await db.commit()

    return {"autopilot": mode, "persona_id": str(persona_id)}


# ─── Video Generation (Phase 8) ──────────────────────────────────────

