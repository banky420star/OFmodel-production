"""Persona Studio — analytics and dashboard routes."""

from __future__ import annotations
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Depends, Query, Form
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    Persona, Identity, AnalyticsSnapshot, Forecast, Shoot, ContentPack,
    PersonaStatus, ShootStatus,
)
from app.schemas import (
    AnalyticsSnapshotResponse, ForecastResponse, ForecastScenario, ManualAnalyticsInput,
)
from app.providers.registry import get_registry
from app.providers.gates import require

router = APIRouter()

@router.get("/dashboard/summary")
async def dashboard_summary(db: AsyncSession = Depends(get_db)):
    """Aggregate dashboard metrics from the database."""
    # Active personas
    personas_result = await db.execute(select(Persona))
    personas = personas_result.scalars().all()
    active_personas = [p for p in personas if p.status in (PersonaStatus.ACTIVE, PersonaStatus.BUILDING)]

    # Shoots
    shoots_result = await db.execute(select(Shoot))
    all_shoots = list(shoots_result.scalars().all())
    active_shoots = [s for s in all_shoots if s.status in (ShootStatus.DRAFT, ShootStatus.GENERATING)]

    # Get persona names for shoots
    persona_map = {str(p.id): p for p in personas}

    # Packs
    packs_result = await db.execute(select(ContentPack))
    packs = packs_result.scalars().all()

    # Analytics totals
    analytics_result = await db.execute(select(AnalyticsSnapshot))
    analytics = analytics_result.scalars().all()
    total_revenue = sum(a.revenue for a in analytics) if analytics else 0
    total_followers = sum(a.followers for a in analytics[-8:]) if analytics else 0
    avg_engagement = (
        sum(a.engagement_rate for a in analytics) / len(analytics)
        if analytics else 0
    )

    # Health
    registry = get_registry()
    health = registry.health_report()
    required = set(registry.required_capabilities())
    required_health = {k: v for k, v in health.items() if k in required}
    services_online = sum(1 for v in required_health.values() if v.get("status") == "green")
    services_total = len(required_health)

    # Build health checks array for frontend
    health_checks = [
        {"service": k, "status": v["status"]}
        for k, v in health.items()
    ]

    # Build shoots list for frontend
    shoots_list = [
        {
            "id": str(s.id),
            "name": s.name,
            "status": s.status.value if hasattr(s.status, 'value') else str(s.status),
            "asset_type": "image",
            "progress": s.progress or 0,
            "image_count": s.image_count or 0,
            "generated_images": s.generated_images or [],
            "theme": s.theme or "",
            "persona_name": persona_map.get(str(s.persona_id), None) and persona_map[str(s.persona_id)].name or "Unknown",
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in all_shoots[:5]
    ]

    # Build persona list for frontend
    personas_list = [
        {
            "id": str(p.id),
            "name": p.name,
            "age": p.age or 0,
            "status": p.status.value if hasattr(p.status, 'value') else str(p.status),
            "brand": p.brand or "",
            "identity_score": None,
            "identity_status": None,
            "packs_count": 0,
            "avatar_url": p.avatar_url or "",
            "shoots_count": len([s for s in all_shoots if str(s.persona_id) == str(p.id)]),
        }
        for p in personas
    ]

    # Attention items (personas needing review)
    attention = []
    for p in personas:
        if p.status == PersonaStatus.BUILDING:
            attention.append({"id": str(p.id), "name": p.name, "type": "building", "status": "warning"})

    return {
        "active_models": len([p for p in active_personas if p.status == PersonaStatus.ACTIVE]),
        "training_models": len([p for p in active_personas if p.status == PersonaStatus.BUILDING]),
        "total_models": len(personas),
        "total_packs": len(packs),
        "total_shoots": len(all_shoots),
        "revenue": round(total_revenue, 2),
        "followers": total_followers,
        "engagement_rate": round(avg_engagement, 4),
        "health": {
            "online": services_online,
            "total": services_total,
            "checks": health_checks,
        },
        "attention_items": attention,
        "shoots": shoots_list,
        "personas": personas_list,
    }



@router.get("/personas/{persona_id}/analytics", response_model=list[AnalyticsSnapshotResponse])
async def get_analytics(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AnalyticsSnapshot)
        .where(AnalyticsSnapshot.persona_id == persona_id)
        .order_by(AnalyticsSnapshot.snapshot_date.desc())
    )
    return [
        AnalyticsSnapshotResponse(
            id=a.id, persona_id=a.persona_id, snapshot_date=a.snapshot_date,
            platform=a.platform, followers=a.followers, likes=a.likes,
            comments=a.comments, shares=a.shares, views=a.views,
            engagement_rate=a.engagement_rate, revenue=a.revenue, costs=a.costs,
        )
        for a in result.scalars().all()
    ]



@router.post("/personas/{persona_id}/analytics/sync")
async def sync_instagram_analytics(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    """Sync real Instagram analytics into the database.

    Pulls real data from the Instagram Graph API and stores it as
    AnalyticsSnapshot records. Returns the synced data.
    """
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    # Real providers only — sync fails closed with the exact env vars to set.
    ig = require("instagram")

    try:
        analytics = await ig.sync_analytics()
    except Exception as e:
        raise HTTPException(502, f"Instagram API error: {e}")

    # Store as AnalyticsSnapshot
    now = datetime.now(timezone.utc)
    snap = AnalyticsSnapshot(
        id=uuid4(),
        persona_id=persona_id,
        snapshot_date=now,
        platform="instagram",
        followers=analytics.profile.followers_count,
        likes=analytics.total_likes,
        comments=analytics.total_comments,
        shares=analytics.total_shares,
        views=analytics.total_impressions,
        engagement_rate=analytics.avg_engagement_rate / 100,  # convert percentage to decimal
        revenue=0,  # Instagram doesn't provide revenue
        costs=0,
    )
    db.add(snap)
    await db.commit()

    return {
        "status": "synced",
        "source": "instagram",
        "synced_at": analytics.synced_at,
        "profile": {
            "username": analytics.profile.username,
            "followers": analytics.profile.followers_count,
            "following": analytics.profile.follows_count,
            "media_count": analytics.profile.media_count,
        },
        "metrics": {
            "total_reach": analytics.total_reach,
            "total_impressions": analytics.total_impressions,
            "total_likes": analytics.total_likes,
            "total_comments": analytics.total_comments,
            "total_saves": analytics.total_saves,
            "total_shares": analytics.total_shares,
            "engagement_rate": analytics.avg_engagement_rate,
        },
        "recent_posts": [
            {
                "id": m.media_id,
                "type": m.media_type,
                "caption": m.caption,
                "likes": m.like_count,
                "comments": m.comments_count,
                "reach": m.reach,
                "saved": m.saved,
            }
            for m in analytics.recent_media[:10]
        ],
    }


@router.post("/personas/{persona_id}/analytics/manual")
async def manual_analytics_entry(
    persona_id: UUID,
    body: ManualAnalyticsInput,
    db: AsyncSession = Depends(get_db),
):
    """Manually enter real analytics data (for platforms without API access)."""
    persona = await db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")

    snap = AnalyticsSnapshot(
        id=uuid4(),
        persona_id=persona_id,
        snapshot_date=datetime.now(timezone.utc),
        platform=body.platform,
        followers=body.followers,
        likes=0,
        comments=0,
        shares=0,
        views=0,
        engagement_rate=body.engagement_rate,
        revenue=body.revenue,
        costs=0,
    )
    db.add(snap)
    await db.commit()

    return {"status": "saved", "source": "manual"}


# ─── Forecasts (Phase 12) ────────────────────────────────────────────

@router.get("/personas/{persona_id}/forecasts", response_model=list[ForecastResponse])
async def get_forecasts(persona_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Forecast).where(Forecast.persona_id == persona_id).order_by(Forecast.created_at.desc())
    )
    forecasts = []
    for f in result.scalars().all():
        scenarios = []
        for scenario_name in ["conservative", "base", "aggressive"]:
            factor = {"conservative": 0.7, "base": 1.0, "aggressive": 1.3}[scenario_name]
            scenarios.append(ForecastScenario(
                scenario=scenario_name,
                horizon_months=f.horizon_months,
                monthly_followers=[int(v * factor) for v in (f.projected_followers or [])],
                monthly_revenue=[round(v * factor, 2) for v in (f.projected_revenue or [])],
                monthly_costs=[round(v * factor, 2) for v in (f.projected_costs or [])],
                monthly_engagement=[round(v * factor, 4) for v in (f.projected_engagement or [])],
                break_even_month=f.metadata_json.get("break_even_month"),
            ))
        forecasts.append(ForecastResponse(
            id=f.id, persona_id=f.persona_id, horizon_months=f.horizon_months,
            scenarios=scenarios, model_version=f.model_version, created_at=f.created_at,
        ))
    return forecasts


# ─── Identities (Phase 2) ───────────────────────────────────────────
