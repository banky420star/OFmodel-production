"""Persona Studio — system routes."""

from __future__ import annotations
import json
import time
import random
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Depends, Query, Form
from sqlalchemy import select, func, text, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    Workflow, WorkflowStep, Job, Persona, QAResult, Identity,
    WorkflowStatus,
)
from app.schemas import (
    WorkflowResponse, WorkflowStepResponse, QAResponse,
    HealthCheck, SystemHealth,
)
from app.providers.registry import get_registry

router = APIRouter()

@router.get("/workflows", response_model=list[WorkflowResponse])
async def list_workflows(
    persona_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(Workflow).order_by(Workflow.created_at.desc())
    if persona_id:
        q = q.where(Workflow.persona_id == persona_id)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/workflows/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: UUID, db: AsyncSession = Depends(get_db)):
    workflow = await db.get(Workflow, workflow_id)
    if not workflow:
        raise HTTPException(404, "Workflow not found")
    return workflow


@router.get("/workflows/{workflow_id}/steps", response_model=list[WorkflowStepResponse])
async def get_workflow_steps(workflow_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(WorkflowStep).where(WorkflowStep.workflow_id == workflow_id).order_by(WorkflowStep.order)
    )
    return result.scalars().all()


# ─── Jobs (Phase 13) ─────────────────────────────────────────────────

@router.get("/jobs")
async def list_jobs(
    persona_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    from app.models import Job
    q = select(Job).order_by(Job.created_at.desc())
    if persona_id:
        q = q.where(Job.persona_id == persona_id)
    result = await db.execute(q)
    jobs = result.scalars().all()
    return [
        {
            "id": str(j.id),
            "persona_id": str(j.persona_id),
            "job_type": j.type,
            "status": j.status,
            "progress": j.progress,
            "current_step": j.metadata_json.get("current_step") if j.metadata_json else None,
            "total_steps": j.metadata_json.get("total_steps") if j.metadata_json else None,
            "result": j.metadata_json.get("result") if j.metadata_json else None,
            "error": j.message,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "updated_at": j.updated_at.isoformat() if j.updated_at else None,
        }
        for j in jobs
    ]


@router.get("/jobs/{job_id}")
async def get_job(job_id: UUID, db: AsyncSession = Depends(get_db)):
    from app.models import Job
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {
        "id": str(job.id),
        "persona_id": str(job.persona_id),
        "job_type": job.type,
        "status": job.status,
        "progress": job.progress,
        "current_step": job.metadata_json.get("current_step") if job.metadata_json else None,
        "total_steps": job.metadata_json.get("total_steps") if job.metadata_json else None,
        "result": job.metadata_json.get("result") if job.metadata_json else None,
        "error": job.message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


# ─── QA (Phase 10) ───────────────────────────────────────────────────

@router.get("/personas/{persona_id}/qa", response_model=list[QAResponse])
async def list_qa_results(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    # QAResult has no persona_id column — reach it through the persona's
    # identities (identity_id) or its workflows (workflow_id).
    result = await db.execute(
        select(QAResult)
        .where(
            or_(
                QAResult.identity_id.in_(
                    select(Identity.id).where(Identity.persona_id == persona_id)
                ),
                QAResult.workflow_id.in_(
                    select(Workflow.id).where(Workflow.persona_id == persona_id)
                ),
            )
        )
        .order_by(QAResult.created_at.desc())
    )
    return result.scalars().all()


# ─── Scheduling (Phase 12) ───────────────────────────────────────────

@router.get("/system/health")
async def system_health():
    """Detailed system health including provider status."""
    registry = get_registry()
    return {
        "providers": registry.health_report(),
        "environment": "development",
    }

# Styles and Health
@router.get("/styles")
async def list_styles(category: str = ""):
    """List available artistic styles for content production.
    
    Returns all 100+ styles grouped by category, or filtered to one category.
    """
    from app.artistic_styles import STYLE_CATEGORIES, ALL_STYLES, count_styles
    
    if category and category in STYLE_CATEGORIES:
        styles = STYLE_CATEGORIES[category]
        return {
            "category": category,
            "count": len(styles),
            "styles": [
                {
                    "name": s.name,
                    "category": s.category,
                    "image_prompt": s.image_prompt,
                    "video_prompt": s.video_prompt,
                    "lighting": s.lighting,
                    "mood": s.mood,
                }
                for s in styles
            ],
        }
    
    return {
        "total": count_styles(),
        "categories": {cat: len(styles) for cat, styles in STYLE_CATEGORIES.items()},
        "styles": [
            {"name": s.name, "category": s.category, "mood": s.mood}
            for s in ALL_STYLES
        ],
    }


# ─── Health ──────────────────────────────────────────────────────────

@router.get("/health")
async def health_check():
    """System health check across all providers."""
    registry = get_registry()
    health = registry.health_report()
    all_green = all(v.get("status") == "green" for v in health.values())

    return {
        "ok": all_green,
        "status": "healthy" if all_green else "degraded",
        "providers": health,
    }


# ─── Fan Chat (Revenue Layer) ────────────────────────────────────────

