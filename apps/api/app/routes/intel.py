"""Market Intelligence routes — observe / patterns / hypotheses.

All data here comes from permitted public sources or our own accounts.
Blocked sources are reported truthfully (see ARCHITECTURE_VISION.md).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.intel import PERMITTED_SOURCES, MarketIntelligence
from app.models import IntelSourceRun, MarketObservation, StrategyHypothesis

router = APIRouter(tags=["intelligence"])


@router.get("/intel/sources")
async def list_sources():
    """Which sources the intelligence network may use, and their status."""
    return {
        "policy": "Permitted public sources + our own accounts only. "
        "Platforms prohibiting crawling are never crawled.",
        "sources": [
            {"name": s.name, "permitted": s.permitted, "note": s.note}
            for s in PERMITTED_SOURCES
        ],
    }


@router.post("/intel/observe")
async def observe(
    niche: str = Query("general", max_length=128),
    db: AsyncSession = Depends(get_db),
):
    """Crawl all permitted sources for a niche and persist observations."""
    mi = MarketIntelligence()
    try:
        summary = await mi.observe(niches=[niche], db=db)
        await db.commit()
    finally:
        await mi.aclose()
    return summary


@router.get("/intel/runs")
async def list_runs(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(IntelSourceRun).order_by(IntelSourceRun.started_at.desc()).limit(limit)
    )
    runs = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "source": r.source,
            "status": r.status,
            "niche": r.niche,
            "query": r.query,
            "items_seen": r.items_seen,
            "observations_new": r.observations_new,
            "detail": r.detail,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in runs
    ]


@router.get("/intel/observations")
async def list_observations(
    niche: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    q = select(MarketObservation).order_by(MarketObservation.observed_at.desc()).limit(limit)
    if niche:
        q = q.where(MarketObservation.niche == niche)
    result = await db.execute(q)
    return [
        {
            "id": str(o.id),
            "source": o.source,
            "niche": o.niche,
            "topic": o.topic,
            "strategy": o.strategy,
            "trend_score": o.trend_score,
            "signal_count": o.signal_count,
            "observed_at": o.observed_at.isoformat() if o.observed_at else None,
        }
        for o in result.scalars().all()
    ]


@router.get("/intel/patterns")
async def get_patterns(
    niche: str = Query("general", max_length=128),
    db: AsyncSession = Depends(get_db),
):
    mi = MarketIntelligence()
    try:
        patterns = await mi.extract_patterns(niche, db)
    finally:
        await mi.aclose()
    return {"niche": niche, "patterns": patterns}


@router.get("/intel/hypotheses")
async def list_hypotheses(
    niche: str | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(StrategyHypothesis).order_by(StrategyHypothesis.created_at.desc()).limit(100)
    if niche:
        q = q.where(StrategyHypothesis.niche == niche)
    if status:
        q = q.where(StrategyHypothesis.status == status)
    result = await db.execute(q)
    return [
        {
            "id": str(h.id),
            "persona_id": str(h.persona_id) if h.persona_id else None,
            "niche": h.niche,
            "status": h.status,
            "statement": h.statement,
            "evidence": h.evidence,
            "experiment": h.experiment,
            "result": h.result,
            "created_at": h.created_at.isoformat() if h.created_at else None,
        }
        for h in result.scalars().all()
    ]


@router.post("/intel/hypotheses/generate")
async def generate_hypotheses(
    niche: str = Query("general", max_length=128),
    db: AsyncSession = Depends(get_db),
):
    """Extract patterns from recent observations and open new hypotheses."""
    mi = MarketIntelligence()
    try:
        created = await mi.generate_hypotheses(niche, db)
        await db.commit()
    finally:
        await mi.aclose()
    return {
        "created": len(created),
        "hypotheses": [
            {
                "id": str(h.id),
                "statement": h.statement,
                "experiment": h.experiment,
            }
            for h in created
        ],
    }
