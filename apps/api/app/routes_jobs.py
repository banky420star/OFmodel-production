"""Persona Studio — Job Routes.

Frontend polls these endpoints to get real-time job progress.
GET /api/v1/jobs          — list recent jobs
GET /api/v1/jobs/{id}     — get specific job with progress
"""

from __future__ import annotations
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Job

router = APIRouter()


@router.get("/jobs")
async def list_jobs(
    persona_id: UUID | None = None,
    status: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List recent jobs, optionally filtered by persona or status."""
    query = select(Job).order_by(Job.created_at.desc()).limit(limit)
    if persona_id:
        query = query.where(Job.persona_id == persona_id)
    if status:
        query = query.where(Job.status == status)
    result = await db.execute(query)
    jobs = result.scalars().all()
    return [
        {
            "id": str(j.id),
            "type": j.type,
            "status": j.status,
            "progress": j.progress,
            "message": j.message,
            "persona_id": str(j.persona_id) if j.persona_id else None,
            "shoot_id": str(j.shoot_id) if j.shoot_id else None,
            "pack_id": str(j.pack_id) if j.pack_id else None,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "updated_at": j.updated_at.isoformat() if j.updated_at else None,
        }
        for j in jobs
    ]


@router.get("/jobs/{job_id}")
async def get_job(job_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get a specific job — frontend polls this every ~1.2s for progress."""
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {
        "id": str(job.id),
        "type": job.type,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "persona_id": str(job.persona_id) if job.persona_id else None,
        "shoot_id": str(job.shoot_id) if job.shoot_id else None,
        "pack_id": str(job.pack_id) if job.pack_id else None,
        "metadata_json": job.metadata_json or {},
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }
