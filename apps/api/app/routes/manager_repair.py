"""Repair utilities for manager pipelines blocked by missing identity references."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.identity_engine import get_identity_reference_path
from app.manager_core import create_task, get_or_create_manager, log_event, set_state
from app.models import (
    AssetStatus,
    ManagerStatus,
    Persona,
    ShotPlan,
)

router = APIRouter(prefix="/manager", tags=["model-manager"])


@router.post("/personas/{persona_id}/repair-identity-reference")
async def repair_identity_reference(
    persona_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Recover blocked GENERATE_IMAGES work after a real reference becomes available.

    This does not fabricate an avatar. It first resolves an existing persisted
    reference (canonical avatar, approved identity reference, non-mock dataset,
    or approved prior shot). Only then are matching blocked shots reset and new
    generation tasks queued. Old blocked ledger rows remain untouched as audit
    history.
    """
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    reference = get_identity_reference_path(persona.id.hex)
    if reference is None:
        raise HTTPException(
            409,
            (
                "No real identity reference is available yet. Upload/restore the "
                "persona's canonical avatar or approve a real identity reference "
                "before retrying production."
            ),
        )

    mgr = await get_or_create_manager(persona.id, db)

    blocked = (await db.execute(
        select(ShotPlan).where(
            ShotPlan.persona_id == persona.id,
            ShotPlan.generation_status == AssetStatus.BLOCKED,
            ShotPlan.error.ilike("%IDENTITY REFERENCE MISSING%"),
        )
    )).scalars().all()

    if not blocked:
        return {
            "persona_id": str(persona.id),
            "reference": str(reference),
            "requeued_shots": 0,
            "generation_tasks_queued": 0,
            "message": "Identity reference is healthy; no matching blocked shots remain.",
        }

    shoot_ids: set[UUID] = set()
    for shot in blocked:
        shot.generation_status = AssetStatus.QUEUED
        shot.error = ""
        shot.retry_count = 0
        shot.qa_status = "pending"
        shoot_ids.add(shot.shoot_id)

    queued = 0
    for shoot_id in shoot_ids:
        _task, created = await create_task(
            db,
            mgr,
            "GENERATE_IMAGES",
            {
                "shoot_id": str(shoot_id),
                "reason": "identity reference recovered; requeue blocked generation",
            },
            source="repair",
            priority=1,
            dedupe=True,
        )
        if created:
            queued += 1

    await set_state(
        db,
        mgr,
        ManagerStatus.RETRYING,
        objective=f"Recovered identity reference: {reference.name}; retrying blocked shoots",
    )
    await log_event(
        db,
        mgr,
        "info",
        "Recovered real identity reference and requeued blocked image generation",
        {
            "reference": str(reference),
            "shots": len(blocked),
            "shoots": len(shoot_ids),
            "tasks_queued": queued,
        },
    )
    await db.commit()

    return {
        "persona_id": str(persona.id),
        "reference": str(reference),
        "requeued_shots": len(blocked),
        "shoots": len(shoot_ids),
        "generation_tasks_queued": queued,
        "next": "manager heartbeat will execute the queued GENERATE_IMAGES task",
    }
